"""审计日志：记录管理员写操作。

提供 log() 便捷函数，在各管理端 service 中调用。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.system import AuditLog, Draft


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            ip=ip,
        )
    )
    db.commit()


def _like_pattern(raw: str) -> str:
    """把用户输入转成安全的 LIKE 模式（转义 % 与 _）。

    否则管理员搜索 `_` 或 `%` 会匹配任意字符/全部行，返回与预期无关的结果。

    Args:
        raw: 用户输入的关键词。

    Returns:
        两侧加通配符、内部通配符已转义的 LIKE 模式。
    """
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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
        stmt = stmt.where(AuditLog.action.like(_like_pattern(action), escape="\\"))
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if from_:
        stmt = stmt.where(AuditLog.created_at >= from_)
    if to:
        stmt = stmt.where(AuditLog.created_at <= to)
    if keyword:
        from sqlalchemy import or_

        kw = _like_pattern(keyword)
        stmt = stmt.where(
            or_(
                AuditLog.action.like(kw, escape="\\"),
                AuditLog.target_type.like(kw, escape="\\"),
                AuditLog.target_id.like(kw, escape="\\"),
            )
        )
    # 部门管理员数据范围过滤：仅看范围内操作者产生的日志
    if scope is not None:
        from app.core.deps import users_in_scope

        scoped_actors = users_in_scope(db, scope)
        if not scoped_actors:
            return [], 0
        stmt = stmt.where(AuditLog.actor.in_(scoped_actors))

    from sqlalchemy import func

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0

    rows = db.execute(stmt.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()

    from app.models.user import User

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
                "actor": f"用户#{u.id}" if u else "系统",
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
    row = db.execute(select(Draft).where(Draft.user_id == user_id, Draft.form_key == form_key)).scalar_one_or_none()
    if row is None:
        row = Draft(user_id=user_id, form_key=form_key, payload=payload, updated_at=_now())
        db.add(row)
    else:
        row.payload = payload
        row.updated_at = _now()
    db.commit()
    return {"success": True, "updated_at": row.updated_at}


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
