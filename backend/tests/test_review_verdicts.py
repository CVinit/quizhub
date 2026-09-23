"""简答复核三种结论的分数正确性回归。

重点覆盖 E1：`verdict="fail"` 且不传 `partial_score` 时，增量必须是 0.0 而非 None。
原实现写 `NULL` 进 NOT NULL 的 `exam_results.score`，触发 IntegrityError → 500，
使「简答判不通过」这一核心动作完全不可用（既有测试只覆盖到"重复复核"分支，未触达加分语句）。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.core.errors import DomainError
from app.core.security import hash_password
from app.database import db_session, init_db
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult, ExamSession, ShortAnswerReview
from app.models.user import User
from app.services import review_service


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mk_user(role: str = "user") -> User:
    return User(
        email=f"{secrets.token_hex(4)}@quizhub.com",
        password_hash=hash_password("pw123456"),
        name="用户",
        role=role,
        status="active",
        email_verified=True,
    )


def _seed(
    db,
    question_scores: list[float],
    *,
    total_score: float = 100.0,
    pass_score: float = 60.0,
    overtime: bool = False,
):
    """建一场含简答的考试 + 成绩 + 每题一条待复核记录。

    Returns:
        (admin, exam, result, reviews, session)
    """
    admin = _mk_user("super_admin")
    student = _mk_user()
    db.add_all([admin, student])
    db.flush()

    bank = QuestionBank(name="简答库", practice_enabled=True)
    db.add(bank)
    db.flush()

    exam = ExamDefinition(
        name="复核考试",
        type="formal",
        rules={},
        group_ids=None,
        duration_min=60,
        pass_score=pass_score,
        max_attempts=0,
        show_score_immediately=True,
        show_analysis=False,
        need_review=True,
        status="reviewing",
        created_by=admin.id,
    )
    db.add(exam)
    db.flush()

    session = ExamSession(
        exam_definition_id=exam.id,
        user_id=student.id,
        status="scoring",
        answers={},
        version=1,
        started_at=_now(),
        submitted_at=_now(),
    )
    db.add(session)
    db.flush()

    result = ExamResult(
        exam_definition_id=exam.id,
        user_id=student.id,
        exam_session_id=session.id,
        score=0,
        total_score=total_score,
        passed=False,
        published=False,
        need_review=True,
        overtime=overtime,
    )
    db.add(result)
    db.flush()

    reviews: list[ShortAnswerReview] = []
    for seq, score in enumerate(question_scores):
        q = Question(
            bank_id=bank.id,
            type="简答题",
            question=f"简答{seq}",
            options=None,
            answer="参考答案",
            analysis="",
            difficulty=1,
            tags=[],
            score=score,
        )
        db.add(q)
        db.flush()
        db.add(ExamQuestion(exam_definition_id=exam.id, question_id=q.id, seq=seq, score=score))
        review = ShortAnswerReview(
            exam_result_id=result.id,
            exam_session_id=session.id,
            user_id=student.id,
            question_id=q.id,
            user_answer="我的作答",
            reference_answer="参考答案",
        )
        db.add(review)
        reviews.append(review)
    db.commit()
    return admin, exam, result, reviews, session


def test_review_fail_without_partial_score_keeps_score():
    """E1 回归：fail 不传 partial_score 时不得写 NULL（否则 NOT NULL → 500）。"""
    init_db()
    with db_session() as db:
        admin, _exam, result, reviews, _sess = _seed(db, [5.0])

        review_service.review(db, reviews[0].id, "fail", None, admin, None)

        db.expire_all()
        after = db.get(ExamResult, result.id)
        assert after.score == 0
        assert after.objective_score == 0
        assert after.score is not None
        row = db.get(ShortAnswerReview, reviews[0].id)
        assert row.verdict == "fail"
        assert row.partial_score is None
        assert row.reviewer == admin.id
        assert row.reviewed_at is not None


def test_review_pass_adds_full_question_score():
    init_db()
    with db_session() as db:
        admin, _exam, result, reviews, _sess = _seed(db, [5.0, 3.0])

        review_service.review(db, reviews[0].id, "pass", None, admin, None)

        db.expire_all()
        after = db.get(ExamResult, result.id)
        assert after.score == 5.0
        # objective_score 是「客观题得分」：简答复核得分不计入该列（见 exam/scoring.py）
        assert after.objective_score == 0


def test_review_partial_adds_given_score():
    init_db()
    with db_session() as db:
        admin, _exam, result, reviews, _sess = _seed(db, [5.0])

        review_service.review(db, reviews[0].id, "partial", 3.0, admin, None)

        db.expire_all()
        after = db.get(ExamResult, result.id)
        assert after.score == 3.0
        assert after.objective_score == 0


def test_review_score_capped_at_total_score():
    """多题累加不得超过满分（历史上会出现 108/100）。"""
    init_db()
    with db_session() as db:
        admin, _exam, result, reviews, _sess = _seed(db, [5.0, 5.0], total_score=5.0)

        review_service.review(db, reviews[0].id, "pass", None, admin, None)
        review_service.review(db, reviews[1].id, "pass", None, admin, None)

        db.expire_all()
        assert db.get(ExamResult, result.id).score == 5.0


@pytest.mark.parametrize(
    ("verdict", "partial_score"),
    [("partial", None), ("partial", -1.0), ("partial", 99.0), ("unknown", None)],
)
def test_review_rejects_invalid_verdict_or_partial_score(verdict, partial_score):
    init_db()
    with db_session() as db:
        admin, _exam, _result, reviews, _sess = _seed(db, [5.0])

        with pytest.raises((DomainError, HTTPException)) as exc:
            review_service.review(db, reviews[0].id, verdict, partial_score, admin, None)
        assert exc.value.status_code == 400


def test_review_rejects_already_reviewed_and_missing_record():
    init_db()
    with db_session() as db:
        admin, _exam, _result, reviews, _sess = _seed(db, [5.0])

        review_service.review(db, reviews[0].id, "pass", None, admin, None)
        with pytest.raises((DomainError, HTTPException)) as dup:
            review_service.review(db, reviews[0].id, "fail", None, admin, None)
        assert dup.value.status_code == 400

        with pytest.raises((DomainError, HTTPException)) as missing:
            review_service.review(db, 999999, "pass", None, admin, None)
        assert missing.value.status_code == 404


def test_review_rejects_question_not_in_exam():
    init_db()
    with db_session() as db:
        admin, exam, result, _reviews, session = _seed(db, [5.0])
        orphan = Question(
            bank_id=None,
            type="简答题",
            question="不属于该考试的题",
            options=None,
            answer="x",
            analysis="",
            difficulty=1,
            tags=[],
            score=2,
        )
        db.add(orphan)
        db.flush()
        review = ShortAnswerReview(
            exam_result_id=result.id,
            exam_session_id=session.id,
            user_id=result.user_id,
            question_id=orphan.id,
            user_answer="a",
            reference_answer="b",
        )
        db.add(review)
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            review_service.review(db, review.id, "pass", None, admin, None)
        assert exc.value.status_code == 400
        assert exam.id  # 保持 exam 被使用，避免未使用变量告警


def test_review_scope_blocks_out_of_range_student():
    init_db()
    with db_session() as db:
        admin, _exam, _result, reviews, _sess = _seed(db, [5.0])

        with pytest.raises((DomainError, HTTPException)) as exc:
            review_service.review(db, reviews[0].id, "pass", None, admin, set())
        assert exc.value.status_code == 403


def test_publish_results_waits_for_pending_then_publishes_and_marks_session_reviewed():
    init_db()
    with db_session() as db:
        admin, exam, result, reviews, session = _seed(db, [5.0], total_score=5.0, pass_score=5.0)

        # 仍有未复核 → 不公布
        assert review_service.publish_results(db, exam.id, None, None) == {"published": 0}
        db.expire_all()
        assert db.get(ExamResult, result.id).published is False

        review_service.review(db, reviews[0].id, "pass", None, admin, None)
        assert review_service.publish_results(db, exam.id, None, None) == {"published": 1}

        db.expire_all()
        after = db.get(ExamResult, result.id)
        assert after.published is True
        assert after.passed is True
        assert db.get(ExamSession, session.id).status == "reviewed"


def test_publish_results_keeps_overtime_failed():
    """超时考试即便复核给满也不判及格。"""
    init_db()
    with db_session() as db:
        admin, exam, result, reviews, _sess = _seed(db, [5.0], total_score=5.0, pass_score=5.0, overtime=True)

        review_service.review(db, reviews[0].id, "pass", None, admin, None)
        review_service.publish_results(db, exam.id, None, None)

        db.expire_all()
        after = db.get(ExamResult, result.id)
        assert after.score == 5.0
        assert after.passed is False


def test_list_pending_filters_by_verdict_and_scope():
    init_db()
    with db_session() as db:
        admin, _exam, _result, reviews, _sess = _seed(db, [5.0, 5.0])

        assert len(review_service.list_pending(db, None, 500, None)) == 2
        review_service.review(db, reviews[0].id, "pass", None, admin, None)
        assert len(review_service.list_pending(db, None, 500, "pending")) == 1
        assert len(review_service.list_pending(db, None, 500, "done")) == 1
        assert [r["verdict"] for r in review_service.list_pending(db, None, 500, "pass")] == ["pass"]
        assert review_service.list_pending(db, None, 500, "partial") == []
        assert review_service.list_pending(db, set(), 500, None) == []
