"""用户导入 Excel 模板生成与解析。

模板 Sheet：用户（表头：邮箱/姓名/角色/初始密码/状态/分组ID）。为支持"先预览后导入"，
预览阶段把解析结果暂存到进程内字典（按 confirm_token），确认阶段读取并落库。
confirm_token 绑定上传用户 id，确认时校验调用者一致，防 IDOR（复用 import_service 模式）。
"""

from __future__ import annotations

import uuid
from io import BytesIO
from typing import Any, TypedDict

from fastapi import status
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.core.email import normalize_email
from app.core.errors import DomainError
from app.core.preview_cache import BoundedTTLCache
from app.core.security import hash_password
from app.utils.excel import MAX_CELL_CHARS, headers_match, open_workbook, validate_workbook_archive

HEADERS = ["邮箱", "姓名", "角色", "初始密码", "状态", "分组ID"]


class UserRowError(TypedDict):
    """用户导入预览的错误项（行号 + 邮箱 + 文案）。"""

    row: int
    email: str
    error: str


# 角色中文 → 英文，便于导入时归一化
ROLE_CN = {
    "普通用户": "user",
    "user": "user",
    "部门管理员": "dept_admin",
    "dept_admin": "dept_admin",
    "超级管理员": "super_admin",
    "super_admin": "super_admin",
}
# 状态中文 → 英文
STATUS_CN = {
    "正常": "active",
    "active": "active",
    "待审批": "pending",
    "pending": "pending",
    "已禁用": "disabled",
    "disabled": "disabled",
}

HEADER_FILL = PatternFill("solid", fgColor="E60012")
HEADER_FONT = Font(color="FFFFFF", bold=True)

# 进程内暂存：confirm_token -> (rows, user_id, ts)。
# 有容量上限与 TTL，避免反复预览却不导入时无界增长（详见 core/preview_cache.py）。
# 注意：存的是 **password_hash** 而非明文口令，模块级缓存中不留可用的初始凭据。
_preview_cache: BoundedTTLCache[list[dict]] = BoundedTTLCache(maxsize=32, ttl=30 * 60)
_IMPORT_ROW_MAX = 5000  # 单次导入用户数上限


def build_template() -> BytesIO:
    """生成用户导入模板 xlsx（含说明）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "用户"
    ws.append(HEADERS)
    for col_idx in range(1, len(HEADERS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    widths = [32, 20, 14, 18, 12, 16]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    # 示例行
    ws.append(["zhangsan@example.com", "张三", "普通用户", "", "正常", ""])
    ws.append(["lisi@example.com", "李四", "部门管理员", "Abc@12345", "正常", "3"])

    # 说明 Sheet
    ws2 = wb.create_sheet("说明")
    notes = [
        ["用户导入模板说明", ""],
        ["", ""],
        ["邮箱", "必填，唯一；重复邮箱该行失败"],
        ["姓名", "可留空，留空时取邮箱 @ 前缀"],
        ["角色", "普通用户 / 部门管理员 / 超级管理员，留空默认普通用户"],
        ["初始密码", "必填，至少 6 位；请通过安全渠道告知用户"],
        ["状态", "正常 / 待审批 / 已禁用，留空默认正常"],
        ["分组ID", "可留空；多个分组用英文逗号分隔，如 3,5"],
        ["", ""],
        ["注意", "角色为管理员需当前操作者是超级管理员；非法分组ID将被忽略"],
    ]
    for row in notes:
        ws2.append(row)
    ws2.column_dimensions["A"].width = 16
    ws2.column_dimensions["B"].width = 60

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _norm_role(v: str) -> str:
    v = (v or "").strip()
    return ROLE_CN.get(v, "user") if v else "user"


def _norm_status(v: str) -> str:
    v = (v or "").strip()
    return STATUS_CN.get(v, "active") if v else "active"


def _parse_group_ids(v: Any) -> list[int]:
    """解析「分组ID」单元格（逗号分隔），非法片段静默跳过。

    片段必须能转为有限整数：`inf` / `1e400` 这类文本会被 float() 解析为 inf，而
    `int(inf)` 抛 OverflowError（原先只捕获 ValueError/TypeError，会让整份导入 500）。
    """
    if v is None or str(v).strip() == "":
        return []
    parts = str(v).replace("，", ",").split(",")
    out: list[int] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        try:
            gid = int(float(p))
            if gid > 0:
                out.append(gid)
        except (ValueError, TypeError, OverflowError):
            continue
    return list(dict.fromkeys(out))


def _parse_row(row: tuple, r_idx: int) -> dict:
    cells = list(row) + [None] * (len(HEADERS) - len(row))
    email = str(cells[0] or "").strip()
    name = str(cells[1] or "").strip()
    role_raw = str(cells[2] or "").strip()
    password = str(cells[3] or "").strip()
    status_raw = str(cells[4] or "").strip()
    group_ids = _parse_group_ids(cells[5])

    error = ""
    if any(isinstance(cell, str) and len(cell) > MAX_CELL_CHARS for cell in cells):
        error = "单元格内容过长"
    elif not email:
        error = "邮箱为空"
    elif any(ch in email for ch in "\r\n"):
        # 换行符会被带进 SMTP 信封/邮件头，属命令注入与头注入素材；在解析阶段就拒绝
        error = "邮箱不能包含换行符"
    elif "@" not in email:
        error = "邮箱格式不正确"

    role = _norm_role(role_raw)
    # 用原始值查映射表判断合法性：_norm_role 对未知值会兜底成 "user"，
    # 若改判 role 本身则该校验永远为假（形同虚设），未知角色会被静默降级为普通用户。
    if role_raw and role_raw not in ROLE_CN:
        error = error or f"角色非法: {role_raw}"

    st = _norm_status(status_raw)
    if status_raw and status_raw not in STATUS_CN:
        error = error or f"状态非法: {status_raw}"

    if not password:
        error = error or "初始密码不能为空"
    elif len(password) < 6:
        error = error or "初始密码至少 6 位"
    elif len(password.encode("utf-8")) > 72:
        # bcrypt 上限：不在这里拦，预览阶段会在 hash_password 处整份文件报错，
        # 且错误信息不指向具体行，其它合法行一起丢失。
        error = error or "初始密码 UTF-8 编码后不能超过 72 字节"

    return {
        "row_index": r_idx,
        # 与全站邮箱口径一致（strip + lower），不再用裸 .lower() 绕过 core.email 的约定
        "email": normalize_email(email),
        "name": name,
        "role": role,
        "password": password,
        "status": st,
        "group_ids": group_ids,
        "valid": not error,
        "error": error,
    }


def preview(content: bytes, user_id: int) -> dict:
    """解析上传工作簿，暂存有效行，返回预览 + confirm_token。

    user_id 必传：预览条目与之绑定，确认导入时校验调用者一致（防 IDOR）。
    """
    validate_workbook_archive(content)
    buf = BytesIO(content)
    wb = open_workbook(buf)
    rows: list[dict] = []
    errors: list[UserRowError] = []
    truncated = False
    try:
        if "用户" in wb.sheetnames:
            ws = wb["用户"]
            # 与题库导入同口径：列按位置取值，表头不一致会让邮箱/口令/分组整体错位
            row_iter = ws.iter_rows(min_row=1, values_only=True)
            header = next(row_iter, None)
            if not headers_match(header, HEADERS):
                errors.append({"row": 1, "email": "", "error": "表头与模板不一致，请下载最新模板后重新填写"})
            else:
                for r_idx, row in enumerate(row_iter, start=2):
                    if r_idx > _IMPORT_ROW_MAX + 1:
                        truncated = True
                        break
                    if not row or all(c is None or str(c).strip() == "" for c in row):
                        continue
                    parsed = _parse_row(row, r_idx)
                    rows.append(parsed)
                    if not parsed["valid"]:
                        errors.append({"row": r_idx, "email": parsed["email"], "error": parsed["error"]})
                    if len(rows) >= _IMPORT_ROW_MAX:
                        truncated = True
                        break
        else:
            # 工作簿里没有「用户」Sheet（表名写错/用了别的模板）：必须给出可见错误，
            # 否则预览会静默返回 0 行，用户完全不知道原因
            errors.append({"row": 0, "email": "", "error": "未找到「用户」工作表，请下载最新模板后重新填写"})
    finally:
        wb.close()

    if truncated:
        # 静默截断会让用户以为已全部导入：补一条可见错误（与题库导入的 truncated 口径一致）
        errors.append(
            {
                "row": _IMPORT_ROW_MAX + 1,
                "email": "",
                "error": f"超过单次导入上限 {_IMPORT_ROW_MAX} 行，其余行未导入",
            }
        )

    valid_rows = [r for r in rows if r["valid"]]
    # 立即把明文口令换成 bcrypt 哈希再暂存：确认导入阶段不再需要明文，
    # 这样即便缓存被读取（堆转储、调试）也不会泄露可用的初始凭据。
    # bcrypt 约 300ms/次，而导入模板里同一初始口令常被大量复用：按口令去重后
    # 只哈希一次，把「行数 × 300ms」降为「不同口令数 × 300ms」。
    hash_cache: dict[str, str] = {}
    cached_rows: list[dict] = []
    for row in valid_rows:
        password = str(row["password"])
        if password not in hash_cache:
            hash_cache[password] = hash_password(password)
        cached_rows.append({**row, "password_hash": hash_cache[password], "password": ""})
    token = uuid.uuid4().hex
    _preview_cache.put(token, cached_rows, owner=float(user_id))
    preview_rows = [{key: value for key, value in row.items() if key != "password"} for row in rows[:50]]
    return {
        "rows": preview_rows,  # 仅返回前 50 条用于预览，避免回传初始密码
        "total": len(rows),
        "valid_count": len(valid_rows),
        "truncated": truncated,
        "errors": errors,
        "confirm_token": token,
    }


def peek_preview(confirm_token: str, user_id: int) -> list[dict]:
    """只读取预览的有效行而不消费（供路由先做角色校验）。

    配合 `consume_preview` 实现「先校验、后消费」：越权或非法请求不应把
    上传者本人的预览作废、逼其重新上传。

    Args:
        confirm_token: 预览令牌。
        user_id: 调用者 id，必须与预览归属一致。

    Returns:
        预览暂存的有效行列表。

    Raises:
        DomainError: 预览不存在/过期（400）或归属不符（403）。
    """
    peeked = _preview_cache.peek(confirm_token)
    if peeked is None:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "预览已过期，请重新上传")
    rows, owner = peeked
    if int(owner) != user_id:
        raise DomainError(status.HTTP_403_FORBIDDEN, "无权导入他人预览数据")
    return rows


def consume_preview(confirm_token: str, user_id: int) -> list[dict]:
    """取出并消费预览暂存的有效行（校验 token 绑定用户）。"""
    peeked = _preview_cache.peek(confirm_token)
    if peeked is None:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "预览已过期，请重新上传")
    _rows, owner = peeked
    if int(owner) != user_id:
        # 不消费他人条目：越权尝试不应使受害者的预览失效
        raise DomainError(status.HTTP_403_FORBIDDEN, "无权导入他人预览数据")
    taken = _preview_cache.take(confirm_token)
    if taken is None:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "预览已过期，请重新上传")
    rows, _owner = taken
    return rows
