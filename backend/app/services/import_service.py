"""题库导入业务：解析→预览→确认导入。

为支持“先预览后导入”，预览阶段把解析结果暂存到内存（按 confirm_token），
确认阶段读取并落库。简单实现用进程内字典（单进程足够）。
confirm_token 绑定上传用户 id，确认时校验调用者一致，防止 IDOR。
"""
from __future__ import annotations

import time
import uuid
from io import BytesIO
from typing import Any

from sqlalchemy.orm import Session

from app.models.question import Question, QuestionBank
from app.schemas.question import UploadImportResult, UploadPreview, UploadPreviewRow
from app.services.question_service import _ensure_tags
from app.utils.excel import parse_workbook


# 进程内暂存：confirm_token -> (rows, group_id, bank_id, bank_name, user_id, ts)
# bank_id 优先；为空则导入时按 bank_name 自动建一个题库（即 question_bank）。
_preview_cache: dict[str, tuple[list[UploadPreviewRow], int | None, int | None, str, int, float]] = {}
# 预览条目有效期（秒），过期不可导入
_PREVIEW_TTL = 30 * 60
# 单次导入行数上限，防止恶意超大文件
_IMPORT_ROW_MAX = 10000


def preview(
    db: Session, content: bytes, group_id: int | None, bank_id: int | None,
    bank_name: str = "", user_id: int = 0,
) -> dict:
    """解析并暂存预览。返回 preview + confirm_token。

    bank_id 优先使用既有题库；否则导入时以 bank_name 命名新建一个题库。
    """
    buf = BytesIO(content)
    preview_obj: UploadPreview = parse_workbook(buf)
    valid_rows = [r for r in _full_rows(preview_obj, buf) if r.valid]
    # 行数上限保护
    truncated = False
    if len(valid_rows) > _IMPORT_ROW_MAX:
        valid_rows = valid_rows[:_IMPORT_ROW_MAX]
        truncated = True

    token = uuid.uuid4().hex
    _preview_cache[token] = (valid_rows, group_id, bank_id, (bank_name or "").strip(), user_id, time.time())
    # 顺手清理过期条目，避免内存泄漏
    _gc_cache()
    return {
        "rows": preview_obj.rows,
        "total": preview_obj.total,
        "valid_count": len(valid_rows),
        "type_dist": preview_obj.type_dist,
        "errors": preview_obj.errors,
        "confirm_token": token,
        "truncated": truncated,
    }


def _gc_cache() -> None:
    """清理过期预览条目。元组结构为 (rows, group_id, bank_id, bank_name, user_id, ts)，ts 在索引 5。"""
    now = time.time()
    expired = [k for k, v in _preview_cache.items() if now - v[5] > _PREVIEW_TTL]
    for k in expired:
        _preview_cache.pop(k, None)


def _full_rows(preview: UploadPreview, buf: BytesIO) -> list[UploadPreviewRow]:
    """parse_workbook 只返回前 20 行预览，这里重新解析取全部。

    使用 read_only 模式 + 行数上限，避免恶意大文件/zip 炸弹耗尽内存。
    """
    from app.utils.excel import SHEET_ORDER, _parse_row
    from openpyxl import load_workbook
    buf.seek(0)
    wb = load_workbook(buf, data_only=True, read_only=True)
    out: list[UploadPreviewRow] = []
    try:
        for sheet_name in SHEET_ORDER:
            if sheet_name not in wb.sheetnames:
                continue
            ws = wb[sheet_name]
            if ws.max_row and ws.max_row > _IMPORT_ROW_MAX + 10:
                # 行数过多直接截断，避免遍历恶意超大表
                pass
            for r_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                if not row or all(c is None or str(c).strip() == "" for c in row):
                    continue
                out.append(_parse_row(sheet_name, row, r_idx))
                if len(out) >= _IMPORT_ROW_MAX:
                    return out
    finally:
        wb.close()
    return out


def do_import(db: Session, confirm_token: str, user_id: int = 0) -> UploadImportResult:
    """根据 confirm_token 把暂存的有效题目落库。校验调用者与 token 绑定一致。

    bank_id 优先；为空则按 bank_name 自动建一个题库（question_bank）作为本次导入归属。
    """
    if confirm_token not in _preview_cache:
        from fastapi import HTTPException, status
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "预览已过期，请重新上传")
    rows, group_id, bank_id, bank_name, owner_id, ts = _preview_cache.pop(confirm_token)
    # token 绑定用户校验（防 IDOR）：仅上传者本人可导入
    if user_id and owner_id and user_id != owner_id:
        from fastapi import HTTPException, status
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权导入他人预览数据")

    # 没有指定既有题库时，按名称自动新建一个题库（同次上传即一个题库）
    if not bank_id:
        bank_name = bank_name or f"题库（{time.strftime('%Y%m%d%H%M%S')}）"
        bank = QuestionBank(name=bank_name, group_id=group_id)
        db.add(bank)
        db.flush()  # 拿到 bank.id
        bank_id = bank.id

    # 先收集所有唯一标签，统一入库，避免同事务内重复插入
    all_tags: set[str] = set()
    for r in rows:
        for t in (r.tags or []):
            all_tags.add(t)
    _ensure_unique_tags(db, list(all_tags))

    success = failed = 0
    pending: list[Question] = []
    for r in rows:
        try:
            q = Question(
                bank_id=bank_id, type=r.type, question=r.question,
                options=r.options, left_items=r.left_items, right_items=r.right_items,
                answer=r.answer, analysis=r.analysis, difficulty=r.difficulty,
                tags=r.tags, score=r.score, group_id=group_id,
            )
            pending.append(q)
            success += 1
        except Exception:
            failed += 1
    # 批量插入，单次 commit（替代原逐题 flush）
    if pending:
        db.add_all(pending)
    db.commit()
    return UploadImportResult(success=success, failed=failed)


def _ensure_unique_tags(db: Session, tags: list[str]) -> None:
    """批量确保标签存在（同事务安全）。"""
    from app.models.question import QuestionTag
    from sqlalchemy import select as sa_select
    if not tags:
        return
    existing = {row[0] for row in db.execute(
        sa_select(QuestionTag.name).where(QuestionTag.name.in_(tags))
    ).all()}
    for name in tags:
        if name and name not in existing:
            db.add(QuestionTag(name=name))
            existing.add(name)
    db.flush()
