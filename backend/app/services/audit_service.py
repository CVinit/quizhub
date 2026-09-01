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
    """写一条审计日志。注意：调用方需自行 commit，或本函数自动 commit。

    为简化调用，本函数自动 commit。
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
        stmt = stmt.where(AuditLog.action.like(f"%{action}%"))
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if from_:
        stmt = stmt.where(AuditLog.created_at >= from_)
    if to:
        stmt = stmt.where(AuditLog.created_at <= to)
    if keyword:
        from sqlalchemy import or_

        kw = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                AuditLog.action.like(kw),
                AuditLog.target_type.like(kw),
                AuditLog.target_id.like(kw),
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

    out = []
    for r in rows:
        u = db.get(User, r.actor) if r.actor else None
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
