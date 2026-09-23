"""2026-09-23 第二轮整改的回归测试（报告 3.1 与未修 P1）。

1. 考试数据范围口径统一：概览计数 / 考试列表 / 操作前校验共用 `exam_in_scope`（子集口径），
   修复「跨部门考试算进概览、却看不到也改不了」的自相矛盾；
2. 题库导入：题目分组继承题库分组（修复 super_admin 指定已有题库时不传 group_id 落 NULL 分组）；
3. 用户导入：部门管理员导入的用户自动归属其部门（与手动新增 create_user 同口径）；
4. 分组更新：显式 `parent_id: null` 不再绕过部门范围守卫；
5. 认证 / 限流失败留痕，且日志中不落邮箱（PII）。
"""

from __future__ import annotations

import logging
import secrets

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import rate_limit
from app.core.deps import dept_scope_ids
from app.core.errors import DomainError
from app.core.security import create_access_token, hash_password
from app.database import SessionLocal, db_session, get_db, init_db
from app.main import create_app
from app.models.exam import ExamDefinition
from app.models.group import Group
from app.models.question import Question, QuestionBank
from app.models.user import User
from app.services import auth_service, exam_service, import_service, question_service, stats_service, user_service
from app.utils.excel import build_template


@pytest.fixture
def api(tmp_path, monkeypatch):
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    monkeypatch.setattr("app.config.FILES_DIR", files_dir)
    monkeypatch.setattr("app.api.system.FILES_DIR", files_dir)

    app = create_app()

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client


def _mk_user(db, email: str, role: str = "user", dept_group_id: int | None = None) -> User:
    user = User(
        email=email,
        password_hash="x",
        name=email.split("@")[0],
        role=role,
        status="active",
        email_verified=True,
        dept_group_id=dept_group_id,
    )
    db.add(user)
    db.flush()
    return user


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, {'ver': user.token_version})}"}


def _setup_org(db) -> tuple[User, User, Group, Group]:
    """集团→{研发部, 市场部}；dept_admin 归属研发部。返回 (dept_admin, super_admin, 研发部, 市场部)。"""
    root = Group(name="集团", type="部门")
    db.add(root)
    db.flush()
    rd = Group(name="研发部", type="部门", parent_id=root.id)
    mk = Group(name="市场部", type="部门", parent_id=root.id)
    db.add_all([rd, mk])
    db.flush()
    dept_admin = _mk_user(db, f"dept{secrets.token_hex(3)}@quizhub.com", "dept_admin", dept_group_id=rd.id)
    super_admin = _mk_user(db, f"super{secrets.token_hex(3)}@quizhub.com", "super_admin")
    db.commit()
    return dept_admin, super_admin, rd, mk


# ---------- 1. 考试数据范围口径统一（报告 3.1） ----------
def test_exam_overview_uses_subset_scope_like_exam_list():
    init_db()
    with db_session() as db:
        admin, _super, rd, mk = _setup_org(db)
        cross = ExamDefinition(name="跨部门考试", type="formal", rules={}, group_ids=[rd.id, mk.id], status="published")
        own = ExamDefinition(name="本部门考试", type="formal", rules={}, group_ids=[rd.id], status="published")
        unassigned = ExamDefinition(name="全员考试", type="formal", rules={}, group_ids=None, status="published")
        db.add_all([cross, own, unassigned])
        db.commit()

        scope = dept_scope_ids(db, admin)
        overview = stats_service.admin_overview(db, scope)
        listed_ids = {e["id"] for e in exam_service.list_exams(db, scope, None, 100)}

        # 概览只统计「完全落在本部门」的考试：跨部门与无指派都不计入
        assert overview["total_exams"] == 1
        # 且与考试列表口径一致（修复前概览会把跨部门考试算进去，列表却看不到）
        assert listed_ids == {own.id}


def test_exam_in_scope_helper_semantics():
    """共享判定的口径：None=全量；无指派不属于任何部门管理员；多分组必须全在范围内。"""
    from app.services.exam_service import exam_in_scope

    assert exam_in_scope(None, None) is True
    assert exam_in_scope([1, 2], None) is True
    assert exam_in_scope(None, {1}) is False
    assert exam_in_scope([], {1}) is False
    assert exam_in_scope([1], {1, 2}) is True
    assert exam_in_scope([1, 3], {1, 2}) is False


# ---------- 2. 题库导入：题目分组继承题库分组 ----------
def test_imported_questions_inherit_bank_group():
    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        bank = QuestionBank(name="已有题库", group_id=group.id)
        db.add(bank)
        db.commit()
        bank_id, group_id = bank.id, group.id

        # super_admin（scope=None）指定已有题库但不传 group_id
        preview = import_service.preview(db, build_template().getvalue(), None, bank_id, "", 1, scope=None)
        result = import_service.do_import(db, preview["confirm_token"], 1, scope=None)
        assert result.success > 0

        questions = db.execute(select(Question).where(Question.bank_id == bank_id)).scalars().all()
        assert {q.group_id for q in questions} == {group_id}
        # 修复前 group_id=NULL 会被部门范围过滤掉，本部门管理员看不到自己题库里的题
        items, total = question_service.list_questions(db, 1, 20, None, None, None, None, None, {group_id})
        assert total == len(questions)


# ---------- 3. 用户导入：自动归属导入者部门 ----------
def test_import_users_assigns_actor_department():
    init_db()
    with db_session() as db:
        admin, _super, rd, _mk = _setup_org(db)
        rows = [
            {
                "email": "imported@quizhub.com",
                "name": "导入用户",
                "role": "user",
                "password": "pw123456",
                "status": "active",
                "group_ids": [],
            }
        ]
        res = user_service.import_users(db, admin.id, rows, scope={rd.id}, actor_role="dept_admin", dept_group_id=rd.id)
        assert res["success"] == 1

        created = db.execute(select(User).where(User.email == "imported@quizhub.com")).scalar_one()
        assert created.dept_group_id == rd.id
        # 部门管理员能在自己的用户列表里看到它（修复前会落成范围外孤儿用户）
        items, _total = user_service.list_users(db, 1, 20, None, None, None, None, {rd.id})
        assert created.id in {u["id"] for u in items}


# ---------- 4. 分组更新：显式 parent_id=null 不再绕过范围守卫 ----------
def test_dept_admin_cannot_move_group_to_root(api):
    init_db()
    with db_session() as db:
        admin, super_admin, rd, _mk = _setup_org(db)
        rd_id, dept_headers, super_headers = rd.id, _headers(admin), _headers(super_admin)

    resp = api.put(f"/api/admin/groups/{rd_id}", json={"parent_id": None}, headers=dept_headers)
    assert resp.status_code == 403, resp.text

    # 未传 parent_id 的普通改名不受影响（PATCH 语义保留）
    renamed = api.put(f"/api/admin/groups/{rd_id}", json={"name": "研发部改名"}, headers=dept_headers)
    assert renamed.status_code == 200, renamed.text

    # super_admin 仍可把分组移到根
    ok = api.put(f"/api/admin/groups/{rd_id}", json={"parent_id": None}, headers=super_headers)
    assert ok.status_code == 200, ok.text


# ---------- 5. 认证 / 限流失败留痕且不落 PII ----------
def test_login_failure_is_logged_without_email(caplog):
    init_db()
    with db_session() as db:
        user = _mk_user(db, "victim@quizhub.com", "user")
        user.password_hash = hash_password("pw123456")
        db.commit()
        user_id = user.id

        with caplog.at_level(logging.WARNING, logger="quizhub"), pytest.raises(DomainError):
            auth_service.login(db, "victim@quizhub.com", "wrong-password")

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "密码错误" in text
    assert f"user_id={user_id}" in text
    assert "victim@quizhub.com" not in text  # PII 不入日志


def test_rate_limit_denial_is_logged_with_masked_key(caplog):
    rate_limit.limiter.reset()
    with caplog.at_level(logging.WARNING, logger="quizhub"), pytest.raises(HTTPException):
        for _ in range(3):
            rate_limit.check("login:account:victim@quizhub.com", 2, 60, "登录")

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "触发限流" in text
    assert "login:account:" in text  # 保留维度，便于定位被刷的入口
    assert "victim@quizhub.com" not in text  # 标识部分只留短哈希
