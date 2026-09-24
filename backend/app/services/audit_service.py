"""审计日志：记录管理员写操作。

提供 log() 便捷函数，在各管理端 service 中调用。
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import exists, func, literal, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.deps import user_ids_subquery
from app.core.errors import DomainError
from app.core.like import ESCAPE_CHAR, like_pattern
from app.core.request_context import get_request_ip
from app.core.status import BAD_REQUEST
from app.core.timeutil import utcnow_iso as _now
from app.models.system import AuditLog, Draft
from app.models.user import User

# 每用户草稿条数上限：form_key 由客户端指定（仅限长度），不设上限时认证用户可以
# 不断换 key 让 drafts 表无界增长（单条体积上限挡不住条数）。
MAX_DRAFTS_PER_USER = 50


def log(
    db: Session,
    actor: int | None,
    action: str,
    target_type: str = "",
    target_id: object = "",
    detail: dict[str, Any] | None = None,
    ip: str = "",
) -> None:
    """写一条审计日志并立即提交。

    来源 IP 默认取自请求上下文（`core.request_context`，由 HTTP 中间件写入）：
    调用方无需、也不应逐层透传 Request/IP。显式传入 `ip` 时以传入值为准
    （供脚本/后台任务在无请求上下文时使用）。

    提交语义说明：调用方绝大多数是在业务操作**已完成提交之后**记录审计，
    此处 commit 主要确保审计行落库（不 commit 会随请求结束回滚而丢失）。
    但这也意味着**调用方不能把审计与业务写入放进同一事务期望原子回滚**：
    若业务失败，请勿调用本函数；若先记审计后业务失败，会留下一条未发生的操作记录。
    因此约定：先完成并提交业务，再记录审计。
    """
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            detail=detail,
            ip=ip or get_request_ip(),
        )
    )
    db.commit()


def list_logs(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    actor: int | None = None,
    action: str | None = None,
    target_type: str | None = None,
    keyword: str | None = None,
    scope: set[int] | None = None,
    from_: str | None = None,
    to: str | None = None,
) -> tuple[list[dict], int]:
    """审计日志列表。

    scope 非 None（部门管理员）时仅返回其数据范围内用户作为操作者的日志，
    系统级（actor 为空）日志仅 super_admin 可见——避免部门管理员窥探其他部门/超管操作。
    """
    stmt = select(AuditLog)
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)
    if action:
        stmt = stmt.where(AuditLog.action.like(like_pattern(action), escape=ESCAPE_CHAR))
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if from_:
        stmt = stmt.where(AuditLog.created_at >= from_)
    if to:
        stmt = stmt.where(AuditLog.created_at <= to)
    if keyword:
        kw = like_pattern(keyword)
        stmt = stmt.where(
            or_(
                AuditLog.action.like(kw, escape=ESCAPE_CHAR),
                AuditLog.target_type.like(kw, escape=ESCAPE_CHAR),
                AuditLog.target_id.like(kw, escape=ESCAPE_CHAR),
            )
        )
    # 部门管理员数据范围过滤：仅看范围内操作者产生的日志。
    # 用子查询而非物化 id 集合，避免大部门触及 SQLite 绑定参数上限；
    # actor IS NULL 的系统日志不在子查询结果中，自然对部门管理员隐藏。
    if scope is not None:
        stmt = stmt.where(AuditLog.actor.in_(user_ids_subquery(scope)))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0

    rows = db.execute(stmt.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()

    # 一次批量取回本页涉及的操作者，消除逐行 db.get 的 N+1
    actor_ids = {r.actor for r in rows if r.actor}
    actor_map = (
        {u.id: u for u in db.execute(select(User).where(User.id.in_(actor_ids))).scalars().all()} if actor_ids else {}
    )

    out = []
    for r in rows:
        u = actor_map.get(r.actor) if r.actor else None
        out.append(
            {
                "id": r.id,
                # actor 为 NULL 有两种来源：真正的系统日志，以及「操作者已被删除」
                # （users 外键 ondelete=SET NULL）。两者无法从本表区分，标签必须如实说明，
                # 否则已删除用户的历史操作会被误读为系统行为。
                "actor": f"用户#{u.id}" if u else "系统/已删除用户",
                "actor_id": r.actor,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "detail": r.detail,
                "ip": r.ip,
                "created_at": r.created_at,
            }
        )
    return out, total


# ---------- 草稿 ----------
def save_draft(db: Session, user_id: int, form_key: str, payload: dict) -> dict:
    """保存草稿（单条 upsert），并限制每用户的草稿条数。

    `drafts` 上有 UNIQUE(user_id, form_key)：原实现是「先 SELECT 判存在、再 INSERT/UPDATE」，
    并发自动保存（多标签页 / 请求重试）会双双读到不存在、双双 INSERT，一方撞唯一约束抛
    IntegrityError → 500。这里用 SQLite 原生 upsert 把读改写收敛成一条原子语句。

    **条数上限同样必须原子**：原先「SELECT COUNT → 判断 → INSERT」在并发下会双双读到
    count = MAX-1 并各自插入，越过唯一的上界。这里把判定放进 INSERT 的 SELECT 里，
    由同一条语句内的 COUNT 决定是否产生行；`rowcount == 0` 即「新增被上限拒绝」
    （覆盖已有 form_key 不受上限影响）。
    """
    now = _now()
    existing_key = exists(select(Draft.id).where(Draft.user_id == user_id, Draft.form_key == form_key))
    user_draft_count = select(func.count()).select_from(Draft).where(Draft.user_id == user_id).scalar_subquery()
    stmt = (
        sqlite_insert(Draft)
        .from_select(
            ["user_id", "form_key", "payload", "updated_at"],
            select(
                literal(user_id),
                literal(form_key),
                literal(payload, type_=Draft.__table__.c.payload.type),
                literal(now),
            ).where(or_(existing_key, user_draft_count < MAX_DRAFTS_PER_USER)),
        )
        .on_conflict_do_update(
            index_elements=["user_id", "form_key"],
            set_={"payload": payload, "updated_at": now},
        )
    )
    result = cast(CursorResult, db.execute(stmt))
    db.commit()
    if result.rowcount == 0:
        raise DomainError(
            BAD_REQUEST,
            f"草稿数量已达上限（{MAX_DRAFTS_PER_USER}），请先清理不再需要的草稿",
        )
    return {"success": True, "updated_at": now}


def load_draft(db: Session, user_id: int, form_key: str) -> dict:
    row = db.execute(select(Draft).where(Draft.user_id == user_id, Draft.form_key == form_key)).scalar_one_or_none()
    if not row:
        return {"exists": False}
    return {"exists": True, "payload": row.payload, "updated_at": row.updated_at}


def clear_draft(db: Session, user_id: int, form_key: str) -> dict:
    row = db.execute(select(Draft).where(Draft.user_id == user_id, Draft.form_key == form_key)).scalar_one_or_none()
    if row:
        db.delete(row)
        db.commit()
    return {"success": True}
