"""分组路由（管理端）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.group import GroupCreate, GroupOut, GroupUpdate
from app.services import group_service
from app.services.audit_service import log as audit_log

router = APIRouter(prefix="/admin/groups", tags=["groups"])


@router.get("")
def list_tree(db: Session = Depends(get_db), _user: User = Depends(require_admin)):
    return group_service.build_tree(db)


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create(payload: GroupCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    g = group_service.create_group(db, payload)
    audit_log(db, user.id, "group.create", "group", g.id, {"name": g.name, "type": g.type})
    return g


@router.put("/{group_id}", response_model=GroupOut)
def update(group_id: int, payload: GroupUpdate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    g = group_service.update_group(db, group_id, payload)
    audit_log(db, user.id, "group.update", "group", group_id, payload.model_dump(exclude_unset=True))
    return g


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(group_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    group_service.delete_group(db, group_id)
    audit_log(db, user.id, "group.delete", "group", group_id)
