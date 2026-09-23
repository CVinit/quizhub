"""分组路由（管理端）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import dept_scope_ids, require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.group import GroupCreate, GroupOut, GroupUpdate
from app.services import group_service
from app.services.audit_service import log as audit_log

router = APIRouter(prefix="/admin/groups", tags=["groups"])


@router.get("")
def list_tree(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return group_service.build_tree(db, dept_scope_ids(db, user))


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create(payload: GroupCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    scope = dept_scope_ids(db, user)
    # 部门管理员只能在自身子树内建子分组（parent 须在 scope 内，或建在根需 super_admin）
    if scope is not None and (payload.parent_id is None or payload.parent_id not in scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权在该分组下创建子分组")
    g = group_service.create_group(db, payload)
    audit_log(db, user.id, "group.create", "group", g.id, {"name": g.name, "type": g.type})
    return g


@router.put("/{group_id}", response_model=GroupOut)
def update(group_id: int, payload: GroupUpdate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    scope = dept_scope_ids(db, user)
    if scope is not None and group_id not in scope:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权操作该分组")
    # 部门管理员不可把分组挂到自身子树外的父分组（防扩张数据范围）。
    # 必须用 model_fields_set 区分「未传」与「显式 null」：显式 null 表示「移到根」，
    # 若不校验，部门管理员可把自己子树内的分组（含 dept_group_id 指向的那个）摘出本部门树。
    if (
        scope is not None
        and "parent_id" in payload.model_fields_set
        and (payload.parent_id is None or payload.parent_id not in scope)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权把分组移出本部门")
    g = group_service.update_group(db, group_id, payload)
    audit_log(db, user.id, "group.update", "group", group_id, payload.model_dump(exclude_unset=True))
    return g


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(group_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    scope = dept_scope_ids(db, user)
    if scope is not None and group_id not in scope:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权操作该分组")
    group_service.delete_group(db, group_id)
    audit_log(db, user.id, "group.delete", "group", group_id)
