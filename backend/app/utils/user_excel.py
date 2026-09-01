"""用户导入 Excel 模板生成与解析。

模板 Sheet：用户（表头：邮箱/姓名/角色/初始密码/状态/分组ID）。为支持"先预览后导入"，
预览阶段把解析结果暂存到进程内字典（按 confirm_token），确认阶段读取并落库。
confirm_token 绑定上传用户 id，确认时校验调用者一致，防 IDOR（复用 import_service 模式）。
"""

from __future__ import annotations

import time
import uuid
from io import BytesIO
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app.services.user_service import ROLES, STATUSES
from app.utils.excel import MAX_CELL_CHARS, validate_workbook_archive

HEADERS = ["邮箱", "姓名", "角色", "初始密码", "状态", "分组ID"]

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

# 进程内暂存：confirm_token -> (rows, user_id, ts)
_preview_cache: dict[str, tuple[list[dict], int, float]] = {}
_PREVIEW_TTL = 30 * 60
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
        except (ValueError, TypeError):
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
    elif "@" not in email:
        error = "邮箱格式不正确"

    role = _norm_role(role_raw)
    if role_raw and role not in ROLES:
        error = error or f"角色非法: {role_raw}"

    st = _norm_status(status_raw)
    if status_raw and st not in STATUSES:
        error = error or f"状态非法: {status_raw}"

    if not password:
        error = error or "初始密码不能为空"
    elif len(password) < 6:
        error = error or "初始密码至少 6 位"

    return {
        "row_index": r_idx,
        "email": email.lower(),
        "name": name,
        "role": role,
        "password": password,
        "status": st,
        "group_ids": group_ids,
        "valid": not error,
        "error": error,
    }


def preview(db: Session, content: bytes, user_id: int = 0) -> dict:
    """解析上传工作簿，暂存有效行，返回预览 + confirm_token。"""
    validate_workbook_archive(content)
    buf = BytesIO(content)
    wb = load_workbook(buf, data_only=True, read_only=True)
    rows: list[dict] = []
    errors: list[dict] = []
    try:
        if "用户" in wb.sheetnames:
            ws = wb["用户"]
            for r_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                if r_idx > _IMPORT_ROW_MAX + 1:
                    break
                if not row or all(c is None or str(c).strip() == "" for c in row):
                    continue
                parsed = _parse_row(row, r_idx)
                rows.append(parsed)
                if not parsed["valid"]:
                    errors.append({"row": r_idx, "email": parsed["email"], "error": parsed["error"]})
                if len(rows) >= _IMPORT_ROW_MAX:
                    break
    finally:
        wb.close()

    valid_rows = [r for r in rows if r["valid"]]
    token = uuid.uuid4().hex
    _preview_cache[token] = (valid_rows, user_id, time.time())
    _gc_cache()
    preview_rows = [{key: value for key, value in row.items() if key != "password"} for row in rows[:50]]
    return {
        "rows": preview_rows,  # 仅返回前 50 条用于预览，避免回传初始密码
        "total": len(rows),
        "valid_count": len(valid_rows),
        "errors": errors,
        "confirm_token": token,
    }


def consume_preview(confirm_token: str, user_id: int) -> list[dict]:
    """取出并消费预览暂存的有效行（校验 token 绑定用户）。"""
    from fastapi import HTTPException
    from fastapi import status as http_status

    if confirm_token not in _preview_cache:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "预览已过期，请重新上传")
    rows, owner_id, created_at = _preview_cache[confirm_token]
    if user_id and owner_id and user_id != owner_id:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "无权导入他人预览数据")
    if time.time() - created_at > _PREVIEW_TTL:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "预览已过期，请重新上传")
    _preview_cache.pop(confirm_token, None)
    return rows


def _gc_cache() -> None:
    now = time.time()
    expired = [k for k, v in _preview_cache.items() if now - v[2] > _PREVIEW_TTL]
    for k in expired:
        _preview_cache.pop(k, None)
