"""鉴权与数据范围测试：dept_admin 仅能操作本部门子树内用户，super_admin 全量。

驱动 backend-code-review Critical 1 的修复：部门管理员不得重置/禁用/审批
其部门子树外的用户（含超级管理员），否则构成垂直/横向越权。
"""

from __future__ import annotations

import secrets

from app.core.deps import dept_scope_ids, user_in_scope
from app.database import db_session, init_db
from app.models.group import Group, UserGroup
from app.models.user import User
from app.services import user_service


def _setup_org(db):
    """构建 集团→{研发部, 市场部} 两棵子树，并各放一个普通用户。

    返回 (dept_admin, dept_admin_user, out_of_scope_user, super_admin)。
    """
    root = Group(name="集团", type="部门")
    db.add(root)
    db.flush()
    rd = Group(name="研发部", type="部门", parent_id=root.id)
    mk = Group(name="市场部", type="部门", parent_id=root.id)
    db.add_all([rd, mk])
    db.flush()
    # 研发部子节点（dept_admin 的范围内）
    rd_team = Group(name="研发一组", type="部门", parent_id=rd.id)
    db.add(rd_team)
    db.flush()

    dept_admin = User(
        email=f"dept{secrets.token_hex(4)}@quizhub.com",
        password_hash="x",
        name="部门管理员",
        role="dept_admin",
        status="active",
        email_verified=True,
        dept_group_id=rd.id,
    )
    in_scope_user = User(
        email=f"rd{secrets.token_hex(4)}@quizhub.com",
        password_hash="x",
        name="研发组员",
        role="user",
        status="active",
        email_verified=True,
    )
    out_user = User(
        email=f"mk{secrets.token_hex(4)}@quizhub.com",
        password_hash="x",
        name="市场组员",
        role="user",
        status="active",
        email_verified=True,
        dept_group_id=mk.id,
    )
    super_admin = User(
        email=f"super{secrets.token_hex(4)}@quizhub.com",
        password_hash="x",
        name="超管",
        role="super_admin",
        status="active",
        email_verified=True,
    )
    db.add_all([dept_admin, in_scope_user, out_user, super_admin])
    db.flush()
    # 研发组员挂在研发一组（研发部子树内）
    db.add(UserGroup(user_id=in_scope_user.id, group_id=rd_team.id))
    db.commit()
    return dept_admin, in_scope_user, out_user, super_admin


def test_dept_admin_scope_excludes_other_dept():
    init_db()
    with db_session() as db:
        dept_admin, in_user, out_user, super_admin = _setup_org(db)
        scope = dept_scope_ids(db, dept_admin)
        assert scope is not None
        # 研发部 + 集团 + 研发一组 都在子树内
        assert dept_admin.dept_group_id in scope
        # 子树内用户可访问
        assert user_in_scope(db, in_user.id, scope) is True
        # 市场部用户不在范围内
        assert user_in_scope(db, out_user.id, scope) is False
        # 超管不在范围内（dept_admin 不能操作超管）
        assert user_in_scope(db, super_admin.id, scope) is False


def test_super_admin_scope_is_none_unlimited():
    init_db()
    with db_session() as db:
        dept_admin, in_user, out_user, super_admin = _setup_org(db)
        scope = dept_scope_ids(db, super_admin)
        assert scope is None
        # None → 全量放行
        assert user_in_scope(db, out_user.id, scope) is True
        assert user_in_scope(db, super_admin.id, scope) is True


def test_dept_admin_cannot_reset_super_admin_password():
    init_db()
    with db_session() as db:
        dept_admin, in_user, out_user, super_admin = _setup_org(db)
        scope = dept_scope_ids(db, dept_admin)
        # 重置超管密码必须被拒
        from fastapi import HTTPException

        try:
            user_service.reset_password(db, dept_admin.id, super_admin.id, "newpass123", scope)
            assert False, "dept_admin 不应能重置超管密码"
        except HTTPException as exc:
            assert exc.status_code == 403
        # 重置市场部用户密码也必须被拒
        try:
            user_service.reset_password(db, dept_admin.id, out_user.id, "newpass123", scope)
            assert False, "dept_admin 不应能重置其他部门用户密码"
        except HTTPException as exc:
            assert exc.status_code == 403


def test_dept_admin_can_reset_in_scope_user():
    init_db()
    with db_session() as db:
        dept_admin, in_user, out_user, super_admin = _setup_org(db)
        scope = dept_scope_ids(db, dept_admin)
        # 范围内用户可重置
        new_pwd = user_service.reset_password(db, dept_admin.id, in_user.id, "newpass123", scope)
        assert new_pwd == "newpass123"
        # 验证密码确实被改
        db.refresh(in_user)
        assert in_user.password_hash != "x"


def test_dept_admin_cannot_disable_out_of_scope_user():
    init_db()
    with db_session() as db:
        dept_admin, in_user, out_user, super_admin = _setup_org(db)
        scope = dept_scope_ids(db, dept_admin)
        from fastapi import HTTPException

        try:
            user_service.set_status(db, dept_admin.id, out_user.id, False, scope)
            assert False, "dept_admin 不应能禁用其他部门用户"
        except HTTPException as exc:
            assert exc.status_code == 403


def test_super_admin_can_reset_anyone():
    init_db()
    with db_session() as db:
        dept_admin, in_user, out_user, super_admin = _setup_org(db)
        scope = dept_scope_ids(db, super_admin)  # None
        # 超管可重置任意人
        new_pwd = user_service.reset_password(db, super_admin.id, out_user.id, "newpass123", scope)
        assert new_pwd == "newpass123"
        new_pwd2 = user_service.reset_password(db, super_admin.id, super_admin.id, "another456", scope)
        assert new_pwd2 == "another456"


def test_dept_admin_list_users_filters_to_scope():
    init_db()
    with db_session() as db:
        dept_admin, in_user, out_user, super_admin = _setup_org(db)
        scope = dept_scope_ids(db, dept_admin)
        items, total = user_service.list_users(db, 1, 100, None, None, None, None, scope)
        emails = {it["email"] for it in items}
        assert in_user.email in emails
        assert out_user.email not in emails
        assert super_admin.email not in emails
