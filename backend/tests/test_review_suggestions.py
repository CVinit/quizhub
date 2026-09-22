"""2026-09 全面后端审查「建议项/可选优化」的回归测试。

覆盖：
- 空卷不可开考/不可发布（S1）；
- 成绩公布幂等（S2）；
- 考试入参边界：duration_min 上限、单题答案体积、verdict 枚举、登录口令长度；
- 审计时间上界归一（S5）；
- 错题本/标记按题库过滤先于 LIMIT（S6）；
- 分组排行按分子/分母加权、展示名不回退邮箱（S7）；
- 管理端完成率按数据范围统计（S8）；批量尝试次数（S9）；范围子查询（S10）；
- random 出题顺序确实打散、统一 UTC 时间戳（N4/N5/N7）。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.api.audit import _iso_bound
from app.core.deps import user_ids_subquery
from app.core.errors import DomainError
from app.core.timeutil import utcnow_iso
from app.database import db_session, init_db
from app.models.exam import ExamDefinition
from app.models.group import Group, UserGroup
from app.models.question import DEFAULT_QUESTION_SCORE, Question, QuestionBank
from app.models.record import ExamResult, ExamSession, QuestionState
from app.models.stats import StatsUserDaily
from app.models.user import User
from app.schemas.auth import LoginIn
from app.schemas.exam import ExamAnswerIn, ExamCreateIn, ExamUpdateIn, ReviewIn
from app.services import exam_service, practice_service, review_service, stats_service
from app.services.exam.common import _attempt_counts, _count_attempts
from app.services.paper_service import generate_paper


def _mk_user(db, name: str = "用户", *, role: str = "user") -> User:
    u = User(
        email=f"{secrets.token_hex(4)}@example.com",
        password_hash="x",
        name=name,
        role=role,
        status="active",
        email_verified=True,
    )
    db.add(u)
    db.flush()
    return u


def _mk_bank(db, name: str = "库", *, enabled: bool = True) -> QuestionBank:
    b = QuestionBank(name=name, practice_enabled=enabled)
    db.add(b)
    db.flush()
    return b


def _mk_question(db, bank: QuestionBank, text: str = "题", *, qtype: str = "单选题", answer: str = "A") -> Question:
    q = Question(
        bank_id=bank.id,
        type=qtype,
        question=text,
        options=["A", "B"],
        answer=answer,
        analysis="",
        difficulty=1,
        tags=[],
        score=DEFAULT_QUESTION_SCORE,
    )
    db.add(q)
    db.flush()
    return q


def _mk_exam(db, *, name: str = "考试", exam_type: str = "mock", rules: dict | None = None) -> ExamDefinition:
    e = ExamDefinition(
        name=name,
        type=exam_type,
        rules=rules or {},
        group_ids=None,
        duration_min=30,
        pass_score=60,
        max_attempts=0,
        show_score_immediately=True,
        show_analysis=False,
        need_review=False,
        status="ongoing",
    )
    db.add(e)
    db.flush()
    return e


def _add_stats(db, user: User, date: str, *, answer: int, correct: int, group_id: int | None = None) -> None:
    db.add(
        StatsUserDaily(
            user_id=user.id,
            date=date,
            group_id=group_id,
            answer_count=answer,
            correct_count=correct,
            wrong_count=answer - correct,
            exam_count=0,
            exam_score_sum=0,
            exam_pass_count=0,
        )
    )


# ---------- S1：空卷 ----------
def test_start_exam_rejects_blank_paper():
    init_db()
    with db_session() as db:
        user = _mk_user(db)
        e = _mk_exam(db, exam_type="formal", rules={})
        db.commit()
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.start_exam(db, user, e.id)
        assert exc.value.status_code == 400


def test_publish_exam_rejects_blank_paper():
    init_db()
    with db_session() as db:
        _mk_user(db, role="super_admin")
        e = _mk_exam(db, exam_type="formal", rules={})
        e.status = "draft"
        db.commit()
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.publish_exam(db, e.id, None, None)
        assert exc.value.status_code == 400


# ---------- S2：公布幂等 ----------
def test_publish_results_is_idempotent():
    init_db()
    with db_session() as db:
        user = _mk_user(db)
        e = _mk_exam(db, exam_type="formal")
        db.add(
            ExamResult(
                exam_definition_id=e.id,
                user_id=user.id,
                score=80,
                total_score=100,
                passed=False,
                need_review=False,
                published=False,
            )
        )
        db.commit()

        assert review_service.publish_results(db, e.id, None, None) == {"published": 1}
        # 第二次调用不应重复统计/重复发信
        assert review_service.publish_results(db, e.id, None, None) == {"published": 0}
        db.expire_all()
        result = db.execute(select(ExamResult).where(ExamResult.exam_definition_id == e.id)).scalar_one()
        assert result.published is True
        assert result.passed is True


def test_publish_results_marks_session_reviewed():
    init_db()
    with db_session() as db:
        user = _mk_user(db)
        e = _mk_exam(db, exam_type="formal")
        sess = ExamSession(
            exam_definition_id=e.id,
            user_id=user.id,
            status="scored",
            answers={},
            version=1,
            started_at=utcnow_iso(),
        )
        db.add(sess)
        db.flush()
        db.add(
            ExamResult(
                exam_definition_id=e.id,
                user_id=user.id,
                exam_session_id=sess.id,
                score=80,
                total_score=100,
                passed=False,
                need_review=False,
                published=False,
            )
        )
        db.commit()

        review_service.publish_results(db, e.id, None, None)
        db.expire_all()
        assert db.get(ExamSession, sess.id).status == "reviewed"


# ---------- 入参边界 ----------
def test_exam_update_duration_capped():
    with pytest.raises(ValidationError):
        ExamUpdateIn(duration_min=2000)
    with pytest.raises(ValidationError):
        ExamCreateIn(name="x", duration_min=2000)


def test_exam_answer_size_is_bounded():
    assert ExamAnswerIn(question_id=1, version=1, answer="A").answer == "A"
    with pytest.raises(ValidationError):
        ExamAnswerIn(question_id=1, version=1, answer="x" * (17 * 1024))


def test_review_verdict_must_be_known_value():
    assert ReviewIn(verdict="pass").verdict == "pass"
    with pytest.raises(ValidationError):
        ReviewIn(verdict="bogus")


def test_login_password_length_is_bounded():
    assert LoginIn(username="a@example.com", password="x" * 72).password
    with pytest.raises(ValidationError):
        LoginIn(username="a@example.com", password="x" * 73)


# ---------- S5：审计时间上界 ----------
def test_iso_bound_normalizes_date_and_offsets():
    assert _iso_bound(None) is None
    assert _iso_bound(datetime(2026, 2, 13)).startswith("2026-02-13T00:00:00")
    # 纯日期作为上界补到当天末尾，否则当天记录会被整体排除
    upper = _iso_bound(datetime(2026, 2, 13), end_of_day=True)
    assert upper.startswith("2026-02-13T23:59:59.999999")
    # 带偏移的时间换算为 UTC
    aware = _iso_bound(datetime(2026, 2, 13, 8, 0, tzinfo=timezone.utc))
    assert aware.startswith("2026-02-13T08:00:00")


# ---------- S6：错题/标记先过滤题库再 LIMIT ----------
def test_wrong_mode_filters_bank_before_limit():
    init_db()
    with db_session() as db:
        bank_a = _mk_bank(db, "A")
        bank_b = _mk_bank(db, "B")
        user = _mk_user(db)
        q_b = _mk_question(db, bank_b, "B题")
        q_a = _mk_question(db, bank_a, "A题")
        # 先写入别的题库的错题，确保「先 LIMIT 再过滤」会返回空
        db.add(QuestionState(user_id=user.id, question_id=q_b.id, status="wrong", marked=False, marked_note=""))
        db.flush()
        db.add(QuestionState(user_id=user.id, question_id=q_a.id, status="wrong", marked=False, marked_note=""))
        db.commit()

        rows = practice_service.start_practice(db, user.id, "wrong", None, 1, bank_a.id)
        assert [r["id"] for r in rows] == [q_a.id]


def test_mark_mode_filters_bank_before_limit():
    init_db()
    with db_session() as db:
        bank_a = _mk_bank(db, "A")
        bank_b = _mk_bank(db, "B")
        user = _mk_user(db)
        q_b = _mk_question(db, bank_b, "B题")
        q_a = _mk_question(db, bank_a, "A题")
        db.add(QuestionState(user_id=user.id, question_id=q_b.id, status="unanswered", marked=True, marked_note=""))
        db.flush()
        db.add(QuestionState(user_id=user.id, question_id=q_a.id, status="unanswered", marked=True, marked_note=""))
        db.commit()

        rows = practice_service.start_practice(db, user.id, "mark", None, 1, bank_a.id)
        assert [r["id"] for r in rows] == [q_a.id]


# ---------- S7：分组排行加权 + 不回退邮箱 ----------
def test_group_accuracy_is_weighted_by_answers():
    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        u1 = _mk_user(db, "甲")
        u2 = _mk_user(db, "乙")
        db.add_all([UserGroup(user_id=u1.id, group_id=group.id), UserGroup(user_id=u2.id, group_id=group.id)])
        today = stats_service._date_str(stats_service._utcnow())
        # 甲 1/1=100%，乙 1/9≈11%：加权正确率应为 2/10=20%，而非平均的平均 55.6%
        _add_stats(db, u1, today, answer=1, correct=1, group_id=group.id)
        _add_stats(db, u2, today, answer=9, correct=1, group_id=group.id)
        db.commit()

        grouped = stats_service.rank(db, "accuracy", "group", "7d")
        row = next(item for item in grouped if item["name"] == "研发部")
        assert row["value"] == 20.0


def test_rank_never_falls_back_to_email():
    init_db()
    with db_session() as db:
        user = _mk_user(db, "")  # 姓名为空
        today = stats_service._date_str(stats_service._utcnow())
        _add_stats(db, user, today, answer=3, correct=2)
        db.commit()

        top = stats_service.rank(db, "count", "self", "7d")
        assert top[0]["user_id"] == user.id
        assert top[0]["name"] == f"#{user.id}"
        assert "@" not in top[0]["name"]


# ---------- S8：概览完成率按范围 ----------
def test_admin_overview_completion_rate_is_scoped():
    init_db()
    with db_session() as db:
        g1 = Group(name="一部", type="部门")
        g2 = Group(name="二部", type="部门")
        db.add_all([g1, g2])
        db.flush()
        u1 = _mk_user(db, "甲")
        u2 = _mk_user(db, "乙")
        db.add_all([UserGroup(user_id=u1.id, group_id=g1.id), UserGroup(user_id=u2.id, group_id=g2.id)])
        bank = _mk_bank(db)
        q = _mk_question(db, bank)
        db.add(QuestionState(user_id=u1.id, question_id=q.id, status="correct", marked=False, marked_note=""))
        db.commit()

        assert stats_service.admin_overview(db, {g1.id})["completion_rate"] == 100
        # 二部没有任何练习：完成率必须为 0（旧实现统计全站，会错误显示 100）
        assert stats_service.admin_overview(db, {g2.id})["completion_rate"] == 0


# ---------- S9/S10：批量尝试次数与范围子查询 ----------
def test_attempt_counts_batches_and_ignores_in_progress():
    init_db()
    with db_session() as db:
        user = _mk_user(db)
        e1 = _mk_exam(db, name="考试1")
        e2 = _mk_exam(db, name="考试2")
        for exam, status in ((e1, "scored"), (e1, "in_progress"), (e2, "submitted")):
            db.add(
                ExamSession(
                    exam_definition_id=exam.id,
                    user_id=user.id,
                    status=status,
                    answers={},
                    version=1,
                    started_at=utcnow_iso(),
                )
            )
        db.commit()

        counts = _attempt_counts(db, [e1.id, e2.id], user.id)
        assert counts == {e1.id: 1, e2.id: 1}
        assert _count_attempts(db, e1.id, user.id) == 1


def test_user_ids_subquery_matches_scope():
    init_db()
    with db_session() as db:
        g1 = Group(name="一部", type="部门")
        g2 = Group(name="二部", type="部门")
        db.add_all([g1, g2])
        db.flush()
        u1 = _mk_user(db, "甲")
        u2 = _mk_user(db, "乙")
        u3 = _mk_user(db, "丙")
        db.add_all([UserGroup(user_id=u1.id, group_id=g1.id), UserGroup(user_id=u2.id, group_id=g2.id)])
        u3.dept_group_id = g1.id
        db.commit()

        ids = set(db.execute(select(User.id).where(User.id.in_(user_ids_subquery({g1.id})))).scalars().all())
        assert ids == {u1.id, u3.id}


# ---------- N4/N5/N7 ----------
def test_order_mode_random_is_shuffled():
    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        for i in range(4):
            _mk_question(db, bank, f"单选{i}")
        for i in range(4):
            _mk_question(db, bank, f"多选{i}", qtype="多选题", answer="AB")
        db.commit()

        quota = {"单选题": 3, "多选题": 3}
        # 同一 seed 保证抽样集合一致，差异只来自最终排序：random 必须整体打散
        bank_order = generate_paper(db, {"type_quota": quota, "order_mode": "bank", "seed": 7})["question_ids"]
        random_order = generate_paper(db, {"type_quota": quota, "order_mode": "random", "seed": 7})["question_ids"]
        assert set(random_order) == set(bank_order)
        assert random_order != bank_order, "random 模式仍然按题型分组输出"


def test_utcnow_iso_is_canonical_utc():
    assert utcnow_iso().endswith("+00:00")
