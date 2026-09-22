"""已固化考试的组卷来源变更语义回归。

产品口径已确认：允许管理员修改，但必须**作废已有作答并按新配置重新固化**，
不能出现「配置改了、考生拿到的还是旧卷」或「成绩与及格线不一致」的静默状态。
由于是破坏性操作，服务端要求显式 `confirm_reset=true`，否则 409 并给出影响面。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.errors import DomainError
from app.core.security import hash_password
from app.database import SessionLocal, db_session, get_db, init_db
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult, ExamSession, ShortAnswerReview
from app.models.stats import StatsUserDaily
from app.models.user import User
from app.services import exam_service, stats_service


def _mk_user(db, role: str = "user", email: str | None = None) -> User:
    user = User(
        email=email or f"{secrets.token_hex(4)}@example.com",
        password_hash=hash_password("pw123456"),
        name="用户",
        role=role,
        status="active",
        email_verified=True,
    )
    db.add(user)
    db.flush()
    return user


def _seed_exam_with_attempt(
    db,
    *,
    quota: int = 2,
    pool: int = 4,
    pass_score: float = 60.0,
    correct: int | None = None,
) -> tuple[User, User, ExamDefinition, ExamSession]:
    """建库/题 + 正式考试 → 学员开考并交卷，返回 (admin, student, exam, session)。

    pass_score 为**百分制**及格线；correct 指定答对几题（None = 全对），
    便于构造「部分得分 + 及格线变更重算」场景。
    """
    bank = QuestionBank(name="库", practice_enabled=True)
    db.add(bank)
    db.flush()
    for i in range(pool):
        db.add(
            Question(
                bank_id=bank.id,
                type="单选题",
                question=f"题{i}",
                options=["甲", "乙"],
                answer="A",
                analysis="",
                difficulty=1,
                tags=[],
                score=2,
            )
        )
    db.flush()

    admin = _mk_user(db, "super_admin", email="super@example.com")
    student = _mk_user(db, email="student@example.com")
    exam = ExamDefinition(
        name="正式考试",
        type="formal",
        rules={"type_quota": {"单选题": quota}},
        group_ids=None,
        duration_min=60,
        pass_score=pass_score,
        max_attempts=0,
        show_score_immediately=True,
        show_analysis=False,
        need_review=False,
        status="published",
        created_by=admin.id,
    )
    db.add(exam)
    db.commit()

    started = exam_service.start_exam(db, student, exam.id)
    session = db.get(ExamSession, started["session_id"])
    questions = started["questions"]
    correct_n = len(questions) if correct is None else correct
    session.answers = {str(q["id"]): {"answer": "A" if idx < correct_n else "B"} for idx, q in enumerate(questions)}
    db.commit()
    exam_service.submit_exam(db, student, session.id)
    db.refresh(session)
    return admin, student, exam, session


def _count(db, model, **filters) -> int:
    stmt = select(func.count()).select_from(model)
    for key, value in filters.items():
        stmt = stmt.where(getattr(model, key) == value)
    return db.execute(stmt).scalar_one()


def test_unrelated_edit_does_not_reset_attempts():
    init_db()
    with db_session() as db:
        _admin, _student, exam, _session = _seed_exam_with_attempt(db)

        exam_service.update_exam(db, exam.id, {"name": "改名了", "duration_min": 90}, None)

        assert db.get(ExamDefinition, exam.id).name == "改名了"
        assert _count(db, ExamSession, exam_definition_id=exam.id) == 1
        assert _count(db, ExamResult, exam_definition_id=exam.id) == 1
        assert _count(db, ExamQuestion, exam_definition_id=exam.id) == 2


def test_source_change_without_confirmation_returns_409_with_impact():
    init_db()
    with db_session() as db:
        _admin, _student, exam, _session = _seed_exam_with_attempt(db)
        before_rules = dict(exam.rules)

        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.update_exam(db, exam.id, {"rules": {"type_quota": {"单选题": 1}}}, None)

        assert exc.value.status_code == 409
        assert isinstance(exc.value.detail, dict)
        assert exc.value.detail["code"] == "exam_reset_required"
        assert exc.value.detail["counts"] == {"attempts": 1, "frozen_questions": 2}
        # 未确认时不得发生任何破坏
        db.expire_all()
        assert db.get(ExamDefinition, exam.id).rules == before_rules
        assert _count(db, ExamSession, exam_definition_id=exam.id) == 1


def test_source_change_with_confirmation_resets_and_refreezes():
    init_db()
    with db_session() as db:
        _admin, student, exam, _session = _seed_exam_with_attempt(db, quota=2, pool=4)
        # 先让当日统计包含这场成绩，验证作废后会重算
        today = stats_service._date_str(datetime.now(timezone.utc))
        stats_service.refresh_daily(db, today)
        assert _count(db, StatsUserDaily, date=today) == 1

        res = exam_service.update_exam(
            db,
            exam.id,
            {"rules": {"type_quota": {"单选题": 1}}, "confirm_reset": True},
            None,
        )

        assert res["reset"] == {"sessions": 1, "results": 1, "reviews": 0, "questions": 2}
        assert _count(db, ExamSession, exam_definition_id=exam.id) == 0
        assert _count(db, ExamResult, exam_definition_id=exam.id) == 0
        assert _count(db, ExamQuestion, exam_definition_id=exam.id) == 0
        # 作废成绩后当日统计已重算，不再残留已作废的分数
        assert _count(db, StatsUserDaily, date=today) == 0

        # 下次开考按新配置重新固化：新卷为 1 题
        started = exam_service.start_exam(db, student, exam.id)
        assert len(started["questions"]) == 1
        assert _count(db, ExamQuestion, exam_definition_id=exam.id) == 1


def test_reset_also_clears_short_answer_reviews():
    init_db()
    with db_session() as db:
        bank = QuestionBank(name="简答库", practice_enabled=True)
        db.add(bank)
        db.flush()
        q = Question(
            bank_id=bank.id,
            type="简答题",
            question="简答",
            options=None,
            answer="参考答案",
            analysis="",
            difficulty=1,
            tags=[],
            score=5,
        )
        db.add(q)
        db.flush()
        admin = _mk_user(db, "super_admin")
        student = _mk_user(db)
        exam = ExamDefinition(
            name="简答考试",
            type="formal",
            rules={},
            manual_questions=[q.id],
            group_ids=None,
            duration_min=60,
            pass_score=6,
            max_attempts=0,
            show_score_immediately=True,
            show_analysis=False,
            need_review=True,
            status="published",
            created_by=admin.id,
        )
        db.add(exam)
        db.commit()

        started = exam_service.start_exam(db, student, exam.id)
        session = db.get(ExamSession, started["session_id"])
        session.answers = {str(q.id): {"answer": "我的作答"}}
        db.commit()
        exam_service.submit_exam(db, student, session.id)
        assert _count(db, ShortAnswerReview, user_id=student.id) == 1

        res = exam_service.update_exam(
            db, exam.id, {"rules": {"type_quota": {"单选题": 1}}, "confirm_reset": True}, None
        )

        assert res["reset"]["reviews"] == 1
        assert _count(db, ShortAnswerReview, user_id=student.id) == 0


def test_pass_score_change_recomputes_published_results_without_reset():
    init_db()
    with db_session() as db:
        # 2 题各 2 分：答对 1 题 → 2/4 = 50%（百分制），pass_score 也是百分制
        _admin, _student, exam, _session = _seed_exam_with_attempt(db, quota=2, pass_score=40.0, correct=1)
        result = db.execute(select(ExamResult).where(ExamResult.exam_definition_id == exam.id)).scalar_one()
        assert result.score == 2
        assert result.total_score == 4
        assert result.passed is True  # 50% >= 40%

        exam_service.update_exam(db, exam.id, {"pass_score": 60}, None)

        db.expire_all()
        result = db.execute(select(ExamResult).where(ExamResult.exam_definition_id == exam.id)).scalar_one()
        assert result.passed is False  # 及格线提高到 60% 后重算
        # 及格线变更不构成组卷来源变更，不应作废作答
        assert _count(db, ExamSession, exam_definition_id=exam.id) == 1

        exam_service.update_exam(db, exam.id, {"pass_score": 30}, None)
        db.expire_all()
        result = db.execute(select(ExamResult).where(ExamResult.exam_definition_id == exam.id)).scalar_one()
        assert result.passed is True  # 50% >= 30%


def test_http_update_exam_requires_confirmation_and_audits_reset():
    """HTTP 层：未确认 409（detail 带 code），确认后 200 且审计留痕。"""
    from fastapi.testclient import TestClient

    from app.core.security import create_access_token
    from app.main import create_app
    from app.models.system import AuditLog

    init_db()
    with SessionLocal() as db:
        _admin, _student, exam, _session = _seed_exam_with_attempt(db)
        admin = db.execute(select(User).where(User.role == "super_admin")).scalar_one()
        token = create_access_token(admin.id, {"role": admin.role, "ver": admin.token_version})
        exam_id = exam.id

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
        blocked = client.put(
            f"/api/admin/exams/{exam_id}", headers=headers, json={"rules": {"type_quota": {"单选题": 1}}}
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "exam_reset_required"

        ok = client.put(
            f"/api/admin/exams/{exam_id}",
            headers=headers,
            json={"rules": {"type_quota": {"单选题": 1}}, "confirm_reset": True},
        )
        assert ok.status_code == 200
        assert ok.json()["reset"]["sessions"] == 1

    with SessionLocal() as db:
        actions = {row[0] for row in db.execute(select(AuditLog.action)).all()}
    assert "exam.reset_attempts" in actions
