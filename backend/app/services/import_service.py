"""题库导入业务：解析→预览→确认导入。

为支持“先预览后导入”，预览阶段把解析结果暂存到内存（按 confirm_token），
确认阶段读取并落库。简单实现用进程内字典（单进程足够）。
confirm_token 绑定上传用户 id，确认时校验调用者一致，防止 IDOR。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from io import BytesIO

from fastapi import status
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.preview_cache import BoundedTTLCache
from app.models.question import Question, QuestionBank
from app.schemas.question import UploadImportResult, UploadPreview, UploadPreviewRow
from app.utils.excel import PARSE_ROW_MAX, parse_workbook


@dataclass(frozen=True, slots=True)
class _PreviewEntry:
    """一次预览暂存的导入参数与解析结果。"""

    rows: list[UploadPreviewRow]
    group_id: int | None
    bank_id: int | None
    bank_name: str


# 进程内暂存：confirm_token -> _PreviewEntry。
# 有容量上限与 TTL（见 core/preview_cache.py），避免反复预览却不导入导致内存无界增长。
# 行数上限由 parse_workbook 的 PARSE_ROW_MAX 限制单条体积，容量上限限制条目数，
# 二者共同给出内存上界。
_preview_cache: BoundedTTLCache[_PreviewEntry] = BoundedTTLCache(maxsize=32, ttl=30 * 60)


def preview(
    db: Session,
    content: bytes,
    group_id: int | None,
    bank_id: int | None,
    bank_name: str,
    user_id: int,
    scope: set[int] | None = None,
) -> dict:
    """解析并暂存预览。返回 preview + confirm_token。

    bank_id 优先使用既有题库；否则导入时以 bank_name 命名新建一个题库。
    user_id 必传：预览条目与之绑定，确认导入时校验调用者一致（防 IDOR）。
    """
    _validate_scope(db, group_id, bank_id, scope)
    buf = BytesIO(content)
    preview_obj: UploadPreview = parse_workbook(buf)
    # 直接使用 parse_workbook 的完整解析结果（`rows` 只是给前端的 20 行预览切片）。
    # 原实现另起一次 `_full_rows()` 重新解析同一份字节流，不仅重复 CPU，还因为
    # 那条路径不做表头校验，使「被 parse_workbook 判定为表头不一致而跳过的行」
    # 仍然进入 valid_rows 并在 do_import 落库（列按位置读取 → 静默错列）。
    valid_rows = [r for r in preview_obj.all_rows if r.valid]
    # 解析阶段触及行数上限时如实上报（可能被截断）
    truncated = preview_obj.total >= PARSE_ROW_MAX

    token = uuid.uuid4().hex
    _preview_cache.put(
        token,
        _PreviewEntry(
            rows=valid_rows,
            group_id=group_id,
            bank_id=bank_id,
            bank_name=(bank_name or "").strip(),
        ),
        owner=float(user_id),
    )
    return {
        "rows": preview_obj.rows,
        "total": preview_obj.total,
        "valid_count": len(valid_rows),
        "type_dist": preview_obj.type_dist,
        "errors": preview_obj.errors,
        "confirm_token": token,
        "truncated": truncated,
    }


def do_import(db: Session, confirm_token: str, user_id: int, scope: set[int] | None = None) -> UploadImportResult:
    """根据 confirm_token 把暂存的有效题目落库。校验调用者与 token 绑定一致。

    bank_id 优先；为空则按 bank_name 自动建一个题库（question_bank）作为本次导入归属。
    """

    # 先 peek（不消费）→ 归属校验 → 数据范围校验 → 最后才 take：
    # 任一校验失败都不应把上传者本人的预览作废、逼其重新上传。
    peeked = _preview_cache.peek(confirm_token)
    if peeked is None:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "预览已过期，请重新上传")
    peeked_entry, owner_id = peeked
    if int(owner_id) != user_id:
        raise DomainError(status.HTTP_403_FORBIDDEN, "无权导入他人预览数据")
    _validate_scope(db, peeked_entry.group_id, peeked_entry.bank_id, scope)

    taken = _preview_cache.take(confirm_token)
    if taken is None:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "预览已过期，请重新上传")
    entry, _owner = taken
    rows = entry.rows
    group_id = entry.group_id
    bank_id = entry.bank_id
    bank_name = entry.bank_name
    # 确认阶段再校验一次：预览与确认之间题库/分组归属可能已被改动
    _validate_scope(db, group_id, bank_id, scope)

    # 没有指定既有题库时，按名称自动新建一个题库（同次上传即一个题库）
    if not bank_id:
        bank_name = bank_name or f"题库（{time.strftime('%Y%m%d%H%M%S')}）"
        bank = QuestionBank(name=bank_name, group_id=group_id)
        db.add(bank)
        db.flush()  # 拿到 bank.id
    else:
        # 预览与确认之间题库可能已被删除：显式报错，避免后续访问 bank 属性变成 500
        existing = db.get(QuestionBank, bank_id)
        if existing is None:
            raise DomainError(status.HTTP_400_BAD_REQUEST, "题库不存在")
        bank = existing
    bank_id = bank.id
    # 题目分组必须与题库分组一致（question_service.update_question 维护同一不变量）。
    # super_admin 指定已有题库但未传 group_id 时若沿用 None，题目会落 NULL 分组：
    # 部门管理员既看不到（列表按 group_id 过滤）也改不了（_validate_group 对 None 直接 403）。
    question_group_id = bank.group_id

    # 先收集所有唯一标签，统一入库，避免同事务内重复插入
    all_tags: set[str] = set()
    for r in rows:
        for t in r.tags or []:
            all_tags.add(t)
    _ensure_unique_tags(db, list(all_tags))

    success = failed = 0
    pending: list[Question] = []
    for r in rows:
        # 题目对象构造只做属性赋值，不会抛业务异常；预览阶段已校验合法性，
        # 故不再用 try/except 吞错——若构造失败应显式报错而非计入 failed=0 掩盖问题。
        q = Question(
            bank_id=bank_id,
            type=r.type,
            question=r.question,
            options=r.options,
            left_items=r.left_items,
            right_items=r.right_items,
            answer=r.answer,
            analysis=r.analysis,
            difficulty=r.difficulty,
            tags=r.tags,
            score=r.score,
            group_id=question_group_id,
        )
        pending.append(q)
        success += 1
    # 批量插入，单次 commit（替代原逐题 flush）
    if pending:
        db.add_all(pending)
    db.commit()
    return UploadImportResult(success=success, failed=failed)


def _validate_scope(db: Session, group_id: int | None, bank_id: int | None, scope: set[int] | None) -> None:
    """校验导入目标分组和题库，且在确认导入时再次执行。"""
    from app.models.group import Group

    if group_id is not None and not db.get(Group, group_id):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "分组不存在")
    if scope is not None and (group_id is None or group_id not in scope):
        raise DomainError(status.HTTP_403_FORBIDDEN, "无权导入到该分组")
    if bank_id:
        bank = db.get(QuestionBank, bank_id)
        if not bank:
            raise DomainError(status.HTTP_400_BAD_REQUEST, "题库不存在")
        if scope is not None and (bank.group_id is None or bank.group_id not in scope):
            raise DomainError(status.HTTP_403_FORBIDDEN, "无权导入到该题库")
        if group_id is not None and bank.group_id is not None and group_id != bank.group_id:
            raise DomainError(status.HTTP_400_BAD_REQUEST, "导入分组必须与题库分组一致")


def _ensure_unique_tags(db: Session, tags: list[str]) -> None:
    """批量确保标签存在（同事务安全）。

    标签名有唯一约束：并发导入同一批标签时，双方都可能在 flush 前读到"不存在"，
    后到者撞唯一约束会让整次导入 500。用 SAVEPOINT 逐条隔离（与
    `question_service._ensure_tags` 同口径），冲突只让该条静默跳过。
    """
    from sqlalchemy import select as sa_select
    from sqlalchemy.exc import IntegrityError

    from app.models.question import QuestionTag

    if not tags:
        return
    existing = {row[0] for row in db.execute(sa_select(QuestionTag.name).where(QuestionTag.name.in_(tags))).all()}
    for name in tags:
        if not name or name in existing:
            continue
        try:
            with db.begin_nested():
                db.add(QuestionTag(name=name))
        except IntegrityError:
            # 并发或重复插入触发唯一约束冲突；SAVEPOINT 已回滚，主事务不受影响
            pass
        existing.add(name)
