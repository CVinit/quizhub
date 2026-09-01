"""FastAPI 依赖：当前用户、角色校验、部门数据范围。"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models.group import Group, UserGroup
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
    try:
        subject = int(payload["sub"])
        token_version = int(payload.get("ver", -1))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "凭证无效或已过期") from None
    user = db.get(User, subject)
    if not user or user.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "用户不可用")
    if token_version != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "凭证已失效，请重新登录")
    return user


def require_role(*roles: str):

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限访问该功能")
        return user

    return _dep


require_admin = require_role("dept_admin", "super_admin")
require_super = require_role("super_admin")


def subtree_ids(db: Session, group_id: int) -> set[int]:
    """返回某分组及其全部后代分组 id（迭代 DFS，避免递归栈溢出）。"""
    ids: set[int] = {group_id}
    stack = [group_id]
    while stack:
        parent = stack.pop()
        children = db.execute(select(Group.id).where(Group.parent_id == parent)).scalars().all()
        for cid in children:
            if cid not in ids:
                ids.add(cid)
                stack.append(cid)
    return ids


# 保留旧名作为兼容别名（历史引用已统一迁移到 subtree_ids）
group_subtree_ids = subtree_ids


def dept_scope_ids(db: Session, user: User) -> set[int] | None:
    """部门管理员的数据范围。

    - super_admin：返回 None 表示全量，不做任何范围限制；
    - dept_admin：返回其 dept_group_id 子树（含自身）的分组 id 集合；
    - 普通用户/未配置部门：返回空集合，表示无任何管理范围。

    供服务层做"按 user_id / group_id 操作前的归属校验"与"列表过滤"。
    """
    if user.role == "super_admin":
        return None
    if user.role == "dept_admin" and user.dept_group_id:
        return subtree_ids(db, user.dept_group_id)
    return set()


def user_group_ids(db: Session, user_id: int) -> set[int]:
    """某用户的全部分组 id（user_groups 直接关联）。"""
    return {r[0] for r in db.execute(select(UserGroup.group_id).where(UserGroup.user_id == user_id)).all()}


def user_in_scope(db: Session, user_id: int, scope: set[int] | None) -> bool:
    """判断目标用户是否在调用者的数据范围内。

    scope 为 None（super_admin）直接放行；否则目标用户需满足：
    其 dept_group_id 或任一 user_groups.group_id 落在 scope 集合内。
    """
    if scope is None:
        return True
    target = db.get(User, user_id)
    if not target:
        return False
    if target.dept_group_id and target.dept_group_id in scope:
        return True
    return bool(user_group_ids(db, user_id) & scope)


def users_in_scope(db: Session, scope: set[int] | None) -> set[int] | None:
    """返回落在数据范围内的全部用户 id 集合。

    scope 为 None（super_admin）返回 None 表示全量，不做范围限制；
    否则返回 dept_group_id ∈ scope 或经 user_groups 关联到 scope 的用户 id 集合。
    供列表/概览查询做按用户维度的范围过滤。
    """
    if scope is None:
        return None
    by_dept = {r[0] for r in db.execute(select(User.id).where(User.dept_group_id.in_(scope))).all()}
    by_group = {r[0] for r in db.execute(select(UserGroup.user_id).where(UserGroup.group_id.in_(scope))).all()}
    return by_dept | by_group
