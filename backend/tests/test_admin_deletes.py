"""用户/考试删除、题型统计与考试编辑 422 回归测试。

驱动本轮 UI 整改的后端支撑：
- delete_user：仅超管、不可删自己/最后一个超管、级联清理练习与考试数据、保留审计与考试定义；
- delete_exam：仅草稿可删、有作答记录拒绝、scope 拦截；
- type_stats：按来源题库统计各题型可用题量（供配比编辑提示）；
- ExamUpdateIn：不接收 type 字段（前端原 payload 带 type 触发 422 的回归）。
"""

from __future__ import annotations

import secrets

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.core.security import hash_password
from app.database import db_session, init_db
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.group import Group, UserGroup
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult, ExamSession
from app.models.system import AuditLog
from app.models.user import User
from app.schemas.exam import ExamUpdateIn
from app.services import exam_service, question_service, user_service


def _mk_user(role="user", **kw):
    return User(
        email=f"{secrets.token_hex(4)}@quizhub.com",
        password_hash=hash_password("pw123456"),
        name=kw.get("name", "用户"),
        role=role,
        status="active",
        email_verified=True,
        **{k: v for k, v in kw.items() if k != "name"},
    )


def _mk_exam(creator_id, status="draft", rules=None):
    return ExamDefinition(
        name="删除测试考试",
        type="formal",
        rules=rules or {},
        group_ids=None,
        duration_min=60,
        pass_score=60,
        max_attempts=0,
        show_score_immediately=True,
        show_analysis=False,
        need_review=False,
        status=status,
        created_by=creator_id,
    )


# ---------- ExamUpdateIn 422 回归 ----------
def test_exam_update_schema_rejects_type_field():
    """前端编辑时的 payload 不含 type（考试类型不可变）；带 type 应校验失败。"""
    ok = ExamUpdateIn.model_validate({"name": "新名称", "duration_min": 45})
    assert ok.name == "新名称"
    with pytest.raises(ValidationError):
        ExamUpdateIn.model_validate({"name": "新名称", "type": "formal"})


def test_exam_update_rejects_explicit_null_on_not_null_columns():
    """duration_min/pass_score/max_attempts 对应 NOT NULL 列。

    显式传 null 必须被拦为校验错误，而不是落库抛 IntegrityError（500）。
    未传（PATCH 语义）仍应通过，且不出现在 exclude_unset 结果中。
    """
    omitted = ExamUpdateIn.model_validate({"name": "仅改名"})
    assert "pass_score" not in omitted.model_dump(exclude_unset=True)

    for field in ("duration_min", "pass_score", "max_attempts"):
        with pytest.raises(ValidationError):
            ExamUpdateIn.model_validate({"name": "x", field: None})


def test_exam_update_rejects_non_object_rules():
    """rules 必须是对象（组卷配置）；null/数组等非对象值应校验失败。"""
    with pytest.raises(ValidationError):
        ExamUpdateIn.model_validate({"rules": None})
    with pytest.raises(ValidationError):
        ExamUpdateIn.model_validate({"rules": []})
    ok = ExamUpdateIn.model_validate({"rules": {"type_quota": {}}})
    assert ok.rules == {"type_quota": {}}


# ---------- 管理端列表需返回可编辑配置 ----------
def test_list_exams_returns_editable_config():
    """管理端列表必须回填 rules/group_ids 等配置，否则编辑弹窗显示为空、保存即清空原配置。"""
    init_db()
    with db_session() as db:
        super_admin = _mk_user("super_admin")
        db.add(super_admin)
        db.flush()
        e = _mk_exam(
            super_admin.id,
            status="published",
            rules={"type_quota": {"单选题": 5}, "bank_ids": [7]},
        )
        e.group_ids = [3]
        e.need_review = True
        e.show_analysis = True
        db.add(e)
        db.commit()

        rows = exam_service.list_exams(db, None)
        row = next(r for r in rows if r["id"] == e.id)
        assert row["rules"] == {"type_quota": {"单选题": 5}, "bank_ids": [7]}
        assert row["group_ids"] == [3]
        assert row["need_review"] is True
        assert row["show_analysis"] is True
        assert "paper_template_id" in row and "manual_questions" in row


def test_user_facing_exam_brief_hides_admin_config():
    """用户端可用考试列表不应泄露组卷配置与指派范围。"""
    init_db()
    with db_session() as db:
        stu = _mk_user()
        super_admin = _mk_user("super_admin")
        db.add_all([stu, super_admin])
        db.flush()
        e = _mk_exam(
            super_admin.id,
            status="published",
            rules={"type_quota": {"单选题": 5}, "bank_ids": [7]},
        )
        e.group_ids = None  # 无指派 = 全员可见
        db.add(e)
        db.commit()

        rows = exam_service.list_available(db, stu)
        row = next(r for r in rows if r["id"] == e.id)
        assert "rules" not in row
        assert "paper_template_id" not in row
        assert "group_ids" not in row


# ---------- 考试删除 ----------
def test_delete_draft_exam_removes_questions():
    init_db()
    with db_session() as db:
        super_admin = _mk_user("super_admin")
        db.add(super_admin)
        db.flush()
        e = _mk_exam(super_admin.id)
        db.add(e)
        db.flush()
        bank = QuestionBank(name="库")
        db.add(bank)
        db.flush()
        q = Question(
            bank_id=bank.id,
            type="单选题",
            question="q",
            options=["A"],
            answer="A",
            analysis="",
            difficulty=1,
            tags=[],
            score=2,
        )
        db.add(q)
        db.flush()
        db.add(ExamQuestion(exam_definition_id=e.id, question_id=q.id, seq=0, score=2, shuffle_map=None))
        db.commit()

        exam_service.delete_exam(db, e.id, None)
        assert db.get(ExamDefinition, e.id) is None
        assert db.execute(select(ExamQuestion).where(ExamQuestion.exam_definition_id == e.id)).first() is None


def test_delete_published_exam_without_sessions_is_allowed():
    """已发布但无人作答的考试（典型为测试考试）可直接删除。

    本轮整改前的规则是「仅草稿可删」，导致已发布的测试考试既不能删又对用户可见；
    现规则改为按有无作答记录判定，无记录即可删（有记录改用归档）。
    """
    init_db()
    with db_session() as db:
        super_admin = _mk_user("super_admin")
        db.add(super_admin)
        db.flush()
        e = _mk_exam(super_admin.id, status="published")
        db.add(e)
        db.commit()
        exam_service.delete_exam(db, e.id, None)
        assert db.get(ExamDefinition, e.id) is None


def test_delete_exam_with_sessions_conflict():
    init_db()
    with db_session() as db:
        super_admin = _mk_user("super_admin")
        stu = _mk_user()
        db.add_all([super_admin, stu])
        db.flush()
        e = _mk_exam(super_admin.id, status="draft")
        db.add(e)
        db.flush()
        db.add(
            ExamSession(
                exam_definition_id=e.id,
                user_id=stu.id,
                answers={},
                version=1,
                started_at="2026-01-01T00:00:00",
                status="in_progress",
            )
        )
        db.commit()
        with pytest.raises(HTTPException) as exc:
            exam_service.delete_exam(db, e.id, None)
        assert exc.value.status_code == 409


def test_delete_exam_scope_denied_for_foreign_dept():
    init_db()
    with db_session() as db:
        root = Group(name="集团", type="部门")
        db.add(root)
        db.flush()
        rd = Group(name="研发部", type="部门", parent_id=root.id)
        mk = Group(name="市场部", type="部门", parent_id=root.id)
        db.add_all([rd, mk])
        db.flush()
        dept_admin = _mk_user("dept_admin", dept_group_id=rd.id)
        db.add(dept_admin)
        db.flush()
        e = _mk_exam(dept_admin.id)
        e.group_ids = [mk.id]  # 指派给市场部：研发部管理员 scope 外
        db.add(e)
        db.commit()
        scope = {rd.id}
        with pytest.raises(HTTPException) as exc:
            exam_service.delete_exam(db, e.id, scope)
        assert exc.value.status_code == 403


# ---------- 用户删除 ----------
def test_delete_user_denied_for_dept_admin():
    init_db()
    with db_session() as db:
        dept_admin = _mk_user("dept_admin")
        stu = _mk_user()
        db.add_all([dept_admin, stu])
        db.commit()
        with pytest.raises(HTTPException) as exc:
            user_service.delete_user(db, dept_admin.id, "dept_admin", stu.id, None)
        assert exc.value.status_code == 403


def test_delete_user_cannot_delete_self_or_last_super():
    init_db()
    with db_session() as db:
        super_admin = _mk_user("super_admin")
        db.add(super_admin)
        db.commit()
        with pytest.raises(HTTPException) as exc:
            user_service.delete_user(db, super_admin.id, "super_admin", super_admin.id, None)
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc:
            user_service.delete_user(db, super_admin.id, "super_admin", super_admin.id, None)
        assert exc.value.status_code == 400  # 唯一超管不可删


def test_delete_user_cascades_but_keeps_exam_and_audit():
    init_db()
    with db_session() as db:
        super_admin = _mk_user("super_admin")
        stu = _mk_user()
        db.add_all([super_admin, stu])
        db.flush()
        root = Group(name="组", type="部门")
        db.add(root)
        db.flush()
        db.add(UserGroup(user_id=stu.id, group_id=root.id))
        e = _mk_exam(stu.id)  # 学生创建的考试
        db.add(e)
        db.flush()
        sess = ExamSession(
            exam_definition_id=e.id,
            user_id=stu.id,
            answers={},
            version=1,
            started_at="2026-01-01T00:00:00",
            status="submitted",
            submitted_at="2026-01-01T01:00:00",
        )
        db.add(sess)
        db.flush()
        db.add(
            ExamResult(
                exam_definition_id=e.id,
                user_id=stu.id,
                exam_session_id=sess.id,
                score=80,
                total_score=100,
                published=True,
            )
        )
        db.commit()
        exam_id = e.id

        user_service.delete_user(db, super_admin.id, "super_admin", stu.id, None)

        assert db.get(User, stu.id) is None
        # 用户侧数据全部清理
        assert db.execute(select(ExamSession).where(ExamSession.user_id == stu.id)).first() is None
        assert db.execute(select(ExamResult).where(ExamResult.user_id == stu.id)).first() is None
        assert db.execute(select(UserGroup).where(UserGroup.user_id == stu.id)).first() is None
        # 考试定义与审计保留，created_by 置空
        kept = db.get(ExamDefinition, exam_id)
        assert kept is not None and kept.created_by is None
        logs = db.execute(select(AuditLog)).scalars().all()
        assert any(log.action == "user.delete" for log in logs)


# ---------- 题型统计 ----------
def test_type_stats_filters_by_bank():
    init_db()
    with db_session() as db:
        b1 = QuestionBank(name="库1")
        b2 = QuestionBank(name="库2")
        db.add_all([b1, b2])
        db.flush()

        def q(bank, qtype, n):
            for i in range(n):
                db.add(
                    Question(
                        bank_id=bank.id,
                        type=qtype,
                        question=f"{qtype}{i}",
                        options=["A", "B"] if "选" in qtype else None,
                        answer="A" if "选" in qtype or qtype == "判断题" else ("对" if False else "A"),
                        analysis="",
                        difficulty=1,
                        tags=[],
                        score=2,
                    )
                )

        q(b1, "单选题", 3)
        q(b1, "多选题", 2)
        q(b2, "单选题", 5)
        db.commit()

        all_stats = question_service.type_stats(db)
        assert all_stats["单选题"] == 8 and all_stats["多选题"] == 2
        b1_stats = question_service.type_stats(db, bank_ids=[b1.id])
        assert b1_stats["单选题"] == 3 and b1_stats["多选题"] == 2 and b1_stats["判断题"] == 0
