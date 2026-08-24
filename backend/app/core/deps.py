"""FastAPI 依赖：当前用户、角色校验、部门数据范围。"""
from __future__ import annotations

from typing import Iterable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models.user import User

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_current_user(
    token: str | None = Depends(oauth2),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "凭证无效或已过期")
    user = db.get(User, int(payload["sub"]))
    if not user or user.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "用户不可用")
    return user


def require_role(*roles: str):
    from functools import wraps

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限访问该功能")
        return user

    return _dep


require_admin = require_role("dept_admin", "super_admin")
require_super = require_role("super_admin")


def group_subtree_ids(db: Session, group_id: int) -> set[int]:
    """返回某分组及其全部后代分组 id（部门管理员数据范围）。"""
    from app.models.group import Group
    ids: set[int] = {group_id}
    stack = [group_id]
    while stack:
        parent = stack.pop()
        children = db.execute(
            Group.__table__.select().where(Group.__table__.c.parent_id == parent)
        ).scalars().all()
        for c in children:
            if c.id not in ids:
                ids.add(c.id)
                stack.append(c.id)
    return ids
