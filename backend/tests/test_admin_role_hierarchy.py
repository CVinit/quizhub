"""管理员账号的层级权限回归。

产品口径已确认：**仅超级管理员可操作管理员账号**。部门管理员的数据范围按部门子树
划定，同一子树内可能有多个 dept_admin；若只看数据范围，同级管理员可以互相禁用、
重置密码（也包括把自己锁死），构成横向越权。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.deps import dept_scope_ids
from app.core.errors import DomainError
from app.core.security import create_access_token, verify_password
from app.database import SessionLocal, db_session, get_db, init_db
from app.main import create_app
from app.models.group import Group, UserGroup
from app.models.user import User
from app.services import user_service


def _mk_user(db, role: str = "user", *, dept_group_id: int | None = None, status: str = "active") -> User:
    seq = db.execute(select(func.count()).select_from(User)).scalar_one()
    user = User(
        email=f"{role}-{dept_group_id}-{seq}@example.com",
        password_hash="x",
        name="用户",
        role=role,
        status=status,
        email_verified=True,
        dept_group_id=dept_group_id,
    )
    db.add(user)
    db.flush()
    return user


def _seed_peers(db):
    """同一部门内：管理员 A、管理员 B（同级）、普通用户 U。"""
    group = Group(name="研发部", type="部门")
    db.add(group)
    db.flush()
    a = _mk_user(db, "dept_admin", dept_group_id=group.id)
    b = _mk_user(db, "dept_admin", dept_group_id=group.id)
    u = _mk_user(db, "user", dept_group_id=group.id)
    super_admin = _mk_user(db, "super_admin")
    db.commit()
    return group, a, b, u, super_admin


def test_dept_admin_cannot_disable_or_reset_peer_admin():
    init_db()
    with db_session() as db:
        group, a, b, _u, _s = _seed_peers(db)
        scope = {group.id}

        for call in (
            lambda: user_service.set_status(db, a.id, b.id, False, scope),
            lambda: user_service.reset_password(db, a.id, b.id, "newpw123", scope),
            lambda: user_service.update_user(db, a.id, b.id, "改名", None, None, scope),
            lambda: user_service.assign_groups(db, a.id, b.id, [], scope),
        ):
            with pytest.raises((DomainError, HTTPException)) as exc:
                call()
            assert exc.value.status_code == 403
            assert "仅超级管理员" in exc.value.detail

        # 目标账号未被改动
        db.expire_all()
        assert db.get(User, b.id).status == "active"
        assert db.get(User, b.id).name == "用户"


def test_dept_admin_cannot_manage_self_or_approve_admin():
    """把自己锁死也在同一守卫内被拦住；待审批的管理员账号同样不能由同级审批。"""
    init_db()
    with db_session() as db:
        group, a, _b, _u, _s = _seed_peers(db)
        scope = {group.id}

        with pytest.raises((DomainError, HTTPException)) as self_disable:
            user_service.set_status(db, a.id, a.id, False, scope)
        assert self_disable.value.status_code == 403

        pending_admin = _mk_user(db, "dept_admin", dept_group_id=group.id, status="pending")
        db.commit()
        with pytest.raises((DomainError, HTTPException)) as approve:
            user_service.approve(db, a.id, pending_admin.id, scope)
        assert approve.value.status_code == 403


def test_dept_admin_can_still_manage_normal_users_in_scope():
    init_db()
    with db_session() as db:
        group, a, _b, u, _s = _seed_peers(db)
        scope = dept_scope_ids(db, a)
        assert scope == {group.id}

        disabled = user_service.set_status(db, a.id, u.id, False, scope)
        assert disabled.status == "disabled"
        enabled = user_service.set_status(db, a.id, u.id, True, scope)
        assert enabled.status == "active"

        user_service.reset_password(db, a.id, u.id, "newpw123", scope)
        db.refresh(u)
        assert verify_password("newpw123", u.password_hash)

        renamed = user_service.update_user(db, a.id, u.id, "新名字", None, None, scope)
        assert renamed.name == "新名字"

        group2 = Group(name="一班", type="班级", parent_id=group.id)
        db.add(group2)
        db.flush()
        # 范围随子树增长：新子分组自动纳入部门管理员的数据范围
        user_service.assign_groups(db, a.id, u.id, [group2.id], dept_scope_ids(db, a))
        links = db.execute(select(UserGroup.group_id).where(UserGroup.user_id == u.id)).scalars().all()
        assert links == [group2.id]


def test_super_admin_can_manage_dept_admin():
    init_db()
    with db_session() as db:
        _group, _a, b, _u, s = _seed_peers(db)

        assert user_service.set_status(db, s.id, b.id, False, None).status == "disabled"
        assert user_service.set_status(db, s.id, b.id, True, None).status == "active"
        user_service.reset_password(db, s.id, b.id, "newpw123", None)
        db.refresh(b)
        assert verify_password("newpw123", b.password_hash)
        assert user_service.update_user(db, s.id, b.id, "管理员改名", None, None, None).name == "管理员改名"


def test_http_dept_admin_cannot_disable_peer_admin():
    """HTTP 层守卫：数据范围允许但角色层级不允许时仍是 403。"""
    from fastapi.testclient import TestClient

    init_db()
    with SessionLocal() as db:
        _group, a, b, _u, _s = _seed_peers(db)
        token = create_access_token(a.id, {"role": a.role, "ver": a.token_version})
        peer_id = b.id
        normal_id = _u.id

    app = create_app()

    def override_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        blocked = client.post(f"/api/admin/users/{peer_id}/disable", headers=headers)
        assert blocked.status_code == 403
        assert "仅超级管理员" in blocked.json()["detail"]

        allowed = client.post(f"/api/admin/users/{normal_id}/disable", headers=headers)
        assert allowed.status_code == 200
        assert allowed.json()["status"] == "disabled"
