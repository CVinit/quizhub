"""越权防护回归测试：分组管理 / 用户导入 / 考试管理的部门数据范围。

驱动本轮（2026-08-28）审计 Critical A1-A6 的修复：
- dept_admin 不可增删改自身子树外的分组；
- dept_admin 不可经 Excel 导入创建管理员账号或分配范围外分组；
- dept_admin 的考试管理/成绩/复核/概览/审计均按 scope 过滤或被拒；
- super_admin 全量放行。
"""

from __future__ import annotations

import secrets

import pytest
from fastapi import HTTPException

from app.core.deps import dept_scope_ids
from app.core.errors import DomainError
from app.database import db_session, init_db
from app.models.group import Group
from app.models.user import User
from app.services import exam_service, stats_service, user_service


def _setup_org(db):
    """集团→{研发部, 市场部}；dept_admin 归属研发部子树。"""
    root = Group(name="集团", type="部门")
    db.add(root)
    db.flush()
    rd = Group(name="研发部", type="部门", parent_id=root.id)
    mk = Group(name="市场部", type="部门", parent_id=root.id)
    db.add_all([rd, mk])
    db.flush()
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
    super_admin = User(
        email=f"super{secrets.token_hex(4)}@quizhub.com",
        password_hash="x",
        name="超管",
        role="super_admin",
        status="active",
        email_verified=True,
    )
    db.add_all([dept_admin, super_admin])
    db.commit()
    return dept_admin, super_admin, rd, mk, rd_team


# ---------- 分组管理越权 ----------
def test_dept_admin_scope_excludes_other_dept():
    """dept_admin 子树不含市场部：越权拦截发生在路由层，此处验证 scope 判定基础。"""
    init_db()
    with db_session() as db:
        dept_admin, super_admin, rd, mk, rd_team = _setup_org(db)
        scope = dept_scope_ids(db, dept_admin)
        assert mk.id not in scope  # 市场部不在 dept_admin 子树
        assert rd.id in scope
        assert rd_team.id in scope


def test_dept_admin_cannot_reparent_into_out_of_scope():
    """dept_admin 不可把分组挂到自身子树外的父分组（防扩张数据范围）。

    必须调用**真实路由**：原实现自己在 `pytest.raises` 里抛 HTTPException，
    等于只断言"Python 能抛异常"，删掉生产守卫该用例依然通过（api/groups.py 的
    守卫零覆盖）。
    """
    init_db()
    from app.api import groups as groups_api
    from app.schemas.group import GroupUpdate

    with db_session() as db:
        dept_admin, _super_admin, _rd, mk, rd_team = _setup_org(db)
        scope = dept_scope_ids(db, dept_admin)
        assert mk.id not in scope, "前提：市场部不在 dept_admin 子树内"

        with pytest.raises((DomainError, HTTPException)) as exc:
            groups_api.update(rd_team.id, GroupUpdate(parent_id=mk.id), db, dept_admin)

    assert exc.value.status_code == 403
    # 目标分组不得被改动
    with db_session() as db:
        assert db.get(Group, rd_team.id).parent_id != mk.id


def test_dept_admin_can_reparent_within_scope():
    """对照：子树内改父分组必须放行（防止守卫被写成一律拒绝）。"""
    init_db()
    from app.api import groups as groups_api
    from app.schemas.group import GroupUpdate

    with db_session() as db:
        dept_admin, _super_admin, rd, _mk, rd_team = _setup_org(db)
        groups_api.update(rd_team.id, GroupUpdate(parent_id=rd.id), db, dept_admin)
        assert db.get(Group, rd_team.id).parent_id == rd.id


# ---------- 用户导入越权 ----------
def test_import_users_blocks_admin_role_for_dept_admin():
    """dept_admin 导入含 super_admin/dept_admin 的行应失败（垂直越权）。"""
    init_db()
    with db_session() as db:
        dept_admin, super_admin, rd, mk, rd_team = _setup_org(db)
        rows = [
            {
                "email": "a@quizhub.com",
                "name": "A",
                "role": "user",
                "status": "active",
                "password": "pass1234",
                "group_ids": [],
            },
            {
                "email": "b@quizhub.com",
                "name": "B",
                "role": "super_admin",
                "status": "active",
                "password": "pass1234",
                "group_ids": [],
            },
        ]
        # actor_role=dept_admin → 第二行应被记为失败
        res = user_service.import_users(db, dept_admin.id, rows, scope=None, actor_role="dept_admin")
        assert res["success"] == 1
        assert res["failed"] == 1
        assert any("仅超级管理员" in e["error"] for e in res["errors"])


def test_import_users_blocks_out_of_scope_group_for_dept_admin():
    """dept_admin 导入用户分配到范围外分组应失败（水平越权）。"""
    init_db()
    with db_session() as db:
        dept_admin, super_admin, rd, mk, rd_team = _setup_org(db)
        scope = dept_scope_ids(db, dept_admin)
        rows = [
            {
                "email": "c@quizhub.com",
                "name": "C",
                "role": "user",
                "status": "active",
                "password": "pass1234",
                "group_ids": [mk.id],  # 市场部，scope 外
            }
        ]
        res = user_service.import_users(db, dept_admin.id, rows, scope=scope, actor_role="dept_admin")
        assert res["success"] == 0
        assert res["failed"] == 1
        assert any("无权分配该分组" in e["error"] for e in res["errors"])


def test_import_users_super_admin_can_import_admin():
    init_db()
    with db_session() as db:
        dept_admin, super_admin, rd, mk, rd_team = _setup_org(db)
        rows = [
            {
                "email": "d@quizhub.com",
                "name": "D",
                "role": "dept_admin",
                "status": "active",
                "password": "pass1234",
                "group_ids": [],
            },
        ]
        res = user_service.import_users(db, super_admin.id, rows, scope=None, actor_role="super_admin")
        assert res["success"] == 1
        assert res["failed"] == 0


# ---------- 考试管理越权 ----------
def _seed_formal_exam(db, creator, group_ids, status="draft"):
    from app.models.exam import ExamDefinition

    e = ExamDefinition(
        name="正式考试",
        type="formal",
        rules={},
        group_ids=group_ids,
        duration_min=60,
        pass_score=60,
        max_attempts=0,
        show_score_immediately=True,
        show_analysis=False,
        need_review=False,
        status=status,
        created_by=creator.id,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def test_list_exams_filters_by_dept_scope():
    """dept_admin 的考试列表只含指派到其子树内的考试；无指派考试不出现。"""
    init_db()
    with db_session() as db:
        dept_admin, super_admin, rd, mk, rd_team = _setup_org(db)
        scope = dept_scope_ids(db, dept_admin)
        e_in = _seed_formal_exam(db, super_admin, group_ids=[rd.id], status="published")
        e_out = _seed_formal_exam(db, super_admin, group_ids=[mk.id], status="published")
        e_none = _seed_formal_exam(db, super_admin, group_ids=None, status="published")
        exams = exam_service.list_exams(db, scope)
        ids = {x["id"] for x in exams}
        assert e_in.id in ids
        assert e_out.id not in ids
        assert e_none.id not in ids  # 无指派考试 dept_admin 不可见
        # super_admin 全量可见
        all_exams = exam_service.list_exams(db, None)
        all_ids = {x["id"] for x in all_exams}
        assert {e_in.id, e_out.id, e_none.id} <= all_ids


def test_update_exam_out_of_scope_rejected():
    init_db()
    with db_session() as db:
        dept_admin, super_admin, rd, mk, rd_team = _setup_org(db)
        scope = dept_scope_ids(db, dept_admin)
        e_out = _seed_formal_exam(db, super_admin, group_ids=[mk.id], status="draft")
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.update_exam(db, e_out.id, {"name": "改"}, scope)
        assert exc.value.status_code == 403


def test_create_exam_out_of_scope_group_ids_rejected():
    init_db()
    with db_session() as db:
        dept_admin, super_admin, rd, mk, rd_team = _setup_org(db)
        scope = dept_scope_ids(db, dept_admin)
        from app.schemas.exam import ExamCreateIn

        payload = ExamCreateIn(name="x", type="formal", group_ids=[mk.id], duration_min=60, pass_score=60)
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.create_exam(db, payload, dept_admin, scope)
        assert exc.value.status_code == 403


def test_publish_exam_out_of_scope_rejected():
    init_db()
    with db_session() as db:
        dept_admin, super_admin, rd, mk, rd_team = _setup_org(db)
        scope = dept_scope_ids(db, dept_admin)
        e_out = _seed_formal_exam(db, super_admin, group_ids=[mk.id], status="draft")
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.publish_exam(db, e_out.id, scope)
        assert exc.value.status_code == 403


# ---------- 概览/审计 scope 过滤 ----------
def test_admin_overview_filters_by_dept_scope():
    """dept_admin 的概览指标仅统计其范围内用户。"""
    init_db()
    with db_session() as db:
        dept_admin, super_admin, rd, mk, rd_team = _setup_org(db)
        # 各放一个用户到研发/市场
        u_rd = User(
            email=f"urd{secrets.token_hex(4)}@quizhub.com",
            password_hash="x",
            name="研发员",
            role="user",
            status="active",
            email_verified=True,
            dept_group_id=rd.id,
        )
        u_mk = User(
            email=f"umk{secrets.token_hex(4)}@quizhub.com",
            password_hash="x",
            name="市场员",
            role="user",
            status="pending",
            email_verified=True,
            dept_group_id=mk.id,
        )
        db.add_all([u_rd, u_mk])
        db.commit()
        scope = dept_scope_ids(db, dept_admin)
        ov_dept = stats_service.admin_overview(db, scope)
        ov_super = stats_service.admin_overview(db, None)
        # dept_admin 至少看到自己 + 范围内 u_rd（2）；看不到 u_mk
        assert ov_dept["total_users"] >= 2
        assert ov_dept["pending_approvals"] == 0  # u_mk 在市场部，不计入
        # super_admin 看到全部（含 pending 的 u_mk）
        assert ov_super["total_users"] >= ov_dept["total_users"] + 1
        assert ov_super["pending_approvals"] >= 1
