"""考试指派范围的子树语义回归。

产品口径已确认：考试指派给父分组时，必须覆盖其子分组成员
（与 dept_admin 数据范围「部门 + 子分组」以及 docs/requirement.md 一致）。
原实现只与用户直属分组求交集，把考试指派给部门对挂在子分组的人不生效。
"""

from __future__ import annotations

import secrets

import pytest
from fastapi import HTTPException

from app.core.errors import DomainError
from app.core.security import hash_password
from app.database import db_session, init_db
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.group import Group, UserGroup
from app.models.question import Question, QuestionBank
from app.models.user import User
from app.services import exam_service


def _mk_user(db, role: str = "user", *, dept_group_id: int | None = None, email: str | None = None) -> User:
    user = User(
        email=email or f"{secrets.token_hex(4)}@example.com",
        password_hash=hash_password("pw123456"),
        name="用户",
        role=role,
        status="active",
        email_verified=True,
        dept_group_id=dept_group_id,
    )
    db.add(user)
    db.flush()
    return user


def _seed_tree(db):
    """部门树：总部 → 研发部 → 一班；另有独立的市场部。"""
    hq = Group(name="总部", type="部门")
    db.add(hq)
    db.flush()
    rd = Group(name="研发部", type="部门", parent_id=hq.id)
    mk = Group(name="市场部", type="部门")
    db.add_all([rd, mk])
    db.flush()
    cls = Group(name="一班", type="班级", parent_id=rd.id)
    db.add(cls)
    db.flush()
    return hq, rd, mk, cls


def _seed_exam(db, *, group_ids: list[int] | None, created_by: int | None = None) -> ExamDefinition:
    bank = QuestionBank(name="库", practice_enabled=True)
    db.add(bank)
    db.flush()
    q = Question(
        bank_id=bank.id,
        type="单选题",
        question="题",
        options=["甲", "乙"],
        answer="A",
        analysis="",
        difficulty=1,
        tags=[],
        score=2,
    )
    db.add(q)
    db.flush()

    exam = ExamDefinition(
        name="部门考试",
        type="formal",
        rules={},
        manual_questions=[q.id],
        group_ids=group_ids,
        duration_min=60,
        pass_score=1,
        max_attempts=0,
        show_score_immediately=True,
        show_analysis=False,
        need_review=False,
        status="published",
        created_by=created_by,
    )
    db.add(exam)
    db.flush()
    db.add(ExamQuestion(exam_definition_id=exam.id, question_id=q.id, seq=0, score=2))
    db.commit()
    return exam


def test_exam_assigned_to_parent_reaches_child_group_member_via_user_groups():
    init_db()
    with db_session() as db:
        hq, rd, _mk, cls = _seed_tree(db)
        admin = _mk_user(db, "super_admin")
        # 学员只挂在子分组「一班」下，不在父分组「研发部」
        student = _mk_user(db)
        db.add(UserGroup(user_id=student.id, group_id=cls.id))
        exam = _seed_exam(db, group_ids=[rd.id], created_by=admin.id)
        db.commit()

        available = exam_service.list_available(db, student)
        assert any(e["id"] == exam.id for e in available)

        # 直接猜 exam_id 开考也应放行（与列表同口径）
        payload = exam_service.start_exam(db, student, exam.id)
        assert payload["session_id"]

        # 祖父分组（总部）指派同样覆盖
        exam.group_ids = [hq.id]
        db.commit()
        assert any(e["id"] == exam.id for e in exam_service.list_available(db, student))


def test_exam_assigned_to_parent_reaches_child_group_member_via_dept_group():
    init_db()
    with db_session() as db:
        _hq, rd, _mk, _cls = _seed_tree(db)
        student = _mk_user(db, dept_group_id=rd.id)  # dept_group_id 直接落在子分组
        exam = _seed_exam(db, group_ids=[])
        exam.group_ids = [rd.id]
        db.commit()

        assert any(e["id"] == exam.id for e in exam_service.list_available(db, student))


def test_exam_assigned_to_child_does_not_reach_parent_group_member():
    """只向下展开，不向上：挂在父分组的用户不应看到指派给子分组的考试。"""
    init_db()
    with db_session() as db:
        _hq, rd, _mk, cls = _seed_tree(db)
        parent_member = _mk_user(db, dept_group_id=rd.id)
        exam = _seed_exam(db, group_ids=[cls.id])
        db.commit()

        assert all(e["id"] != exam.id for e in exam_service.list_available(db, parent_member))
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.start_exam(db, parent_member, exam.id)
        assert exc.value.status_code == 403


def test_exam_assigned_to_unrelated_group_is_still_blocked():
    init_db()
    with db_session() as db:
        _hq, rd, mk, _cls = _seed_tree(db)
        outsider = _mk_user(db, dept_group_id=mk.id)
        exam = _seed_exam(db, group_ids=[rd.id])
        db.commit()

        assert all(e["id"] != exam.id for e in exam_service.list_available(db, outsider))
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.start_exam(db, outsider, exam.id)
        assert exc.value.status_code == 403


def test_publish_notification_reaches_child_group_members():
    """发布通知的收件人集合必须与可见性同口径（否则子分组成员收不到通知）。"""
    init_db()
    with db_session() as db:
        _hq, rd, mk, cls = _seed_tree(db)
        _mk_user(db, "super_admin", email="admin@example.com")
        child_member = _mk_user(db, email="child@example.com")
        _mk_user(db, email="outsider@example.com", dept_group_id=mk.id)
        db.add(UserGroup(user_id=child_member.id, group_id=cls.id))
        exam = _seed_exam(db, group_ids=[rd.id])
        exam.status = "draft"
        db.commit()

        sent: list[tuple] = []

        class _Bg:
            def add_task(self, fn, *args, **kwargs):
                sent.append((fn, args))

        exam_service.publish_exam(db, exam.id, None, _Bg())

        # 发布通知收敛为一个批量后台任务：args = (send_exam_publish_many, settings, recipients, name, end_at)
        assert len(sent) == 1
        _fn, args = sent[0]
        recipients = set(args[2])
        assert "child@example.com" in recipients
        assert "outsider@example.com" not in recipients
        assert "admin@example.com" not in recipients  # 超管不在指派范围内则不通知


def test_exam_without_assignment_is_open_to_everyone():
    init_db()
    with db_session() as db:
        _hq, _rd, mk, _cls = _seed_tree(db)
        anyone = _mk_user(db, dept_group_id=mk.id)
        exam = _seed_exam(db, group_ids=None)
        db.commit()

        assert any(e["id"] == exam.id for e in exam_service.list_available(db, anyone))
