"""审计日志与草稿路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_admin
from app.database import get_db
from app.models.user import User
from app.services import audit_service

router = APIRouter(tags=["audit"])


# ---------- 审计日志（管理端）----------
@router.get("/admin/audit-logs")
def list_logs(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    actor: int | None = None, action: str | None = None,
    target_type: str | None = None, keyword: str | None = None,
    db: Session = Depends(get_db), _user: User = Depends(require_admin),
):
    items, total = audit_service.list_logs(
        db, page, page_size, actor, action, target_type, keyword,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ---------- 草稿（用户端，长表单自动保存）----------
@router.get("/drafts/{form_key}")
def load_draft(form_key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return audit_service.load_draft(db, user.id, form_key)


@router.put("/drafts/{form_key}")
def save_draft(form_key: str, payload: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return audit_service.save_draft(db, user.id, form_key, payload)


@router.delete("/drafts/{form_key}")
def clear_draft(form_key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return audit_service.clear_draft(db, user.id, form_key)
