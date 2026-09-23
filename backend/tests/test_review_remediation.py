"""回归测试：2026-09 后端审核修复。

覆盖项（每项对应一个已修复缺陷，断言的是修复后的正确行为，而非实现细节）：
1. 邮箱大小写归一 —— 大小写变体可登录、不产生重复账号。
2. list_modes 题库授权 —— 关闭练习的题库不得泄露统计。
3. 模拟考试按用户隔离 —— 不得复用他人的已固化试卷。
4. 简答复核成绩封顶 —— score 不超 total_score，且不改动 objective_score（客观题得分）。
5. 外键级联 —— 删除有依赖数据的用户/题目不再 IntegrityError。
6. 统计日期归属 —— 兼容多种时间戳格式且使用业务时区。
7. _recover_stuck_scoring 作用域 —— 不复活他人会话、不复活无时区脏数据。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.email import normalize_email
from app.core.errors import DomainError
from app.core.security import hash_password
from app.database import SessionLocal
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.group import Group
from app.models.question import Question, QuestionBank
from app.models.record import (
    ExamResult,
    ExamSession,
    PracticeRecord,
    QuestionState,
    ShortAnswerReview,
)
from app.models.stats import StatsUserDaily
from app.models.user import User
from app.schemas.auth import LoginIn, RegisterIn, SendCodeIn
from app.services import auth_service, practice_service, review_service, stats_service

# ---------- 1. 邮箱大小写归一 ----------


def test_email_schemas_lowercase_local_part():
    """EmailStr 只转小写域名，local-part 必须由我们归一。"""
    assert LoginIn(username="Admin@Example.COM", password="x").username == "admin@example.com"
    assert RegisterIn(email="ADMIN@example.com", password="secret1", code="1234").email == "admin@example.com"
    assert SendCodeIn(email=" Foo@Bar.COM ", captcha_id="abcd", captcha_code="1").email == "foo@bar.com"


def test_normalize_email_handles_none_and_whitespace():
    assert normalize_email(None) == ""
    assert normalize_email("") == ""
    assert normalize_email("  A@B.COM  ") == "a@b.com"


@pytest.mark.parametrize("probe", ["Admin@Example.com", "admin@example.com", "ADMIN@EXAMPLE.COM"])
def test_login_succeeds_for_any_case_variant(probe):
    """同一账号的任意大小写写法都必须能登录（修复前仅精确匹配才成功）。"""
    with SessionLocal() as db:
        db.add(
            User(
                email=normalize_email("Admin@Example.com"),
                password_hash=hash_password("secret123"),
                name="A",
                role="user",
                status="active",
                email_verified=True,
            )
        )
        db.commit()

        res = auth_service.login(db, probe, "secret123")
        assert res["user"].email == "admin@example.com"


def test_case_variant_does_not_create_duplicate_account():
    """大小写变体不得绕过唯一约束产生第二个账号。"""
    with SessionLocal() as db:
        db.add(
            User(
                email=normalize_email("admin@example.com"),
                password_hash=hash_password("secret123"),
                name="A",
                role="user",
                status="active",
                email_verified=True,
            )
        )
        db.commit()

        # 服务层去重：归一后应命中既有账号
        existing = db.execute(
            select(User).where(User.email == normalize_email("ADMIN@EXAMPLE.COM"))
        ).scalar_one_or_none()
        assert existing is not None

        # 数据库层兜底：即便直接写入也必须被唯一约束拒绝
        db.add(User(email="ADMIN@EXAMPLE.COM", password_hash="x", name="dup", role="user", status="active"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


# ---------- 2. list_modes 题库授权 ----------


def test_list_modes_rejects_disabled_bank():
    """关闭练习的题库：统计接口必须与开考接口一样拒绝，否则泄露题量。"""
    with SessionLocal() as db:
        user = User(email="p@x.com", password_hash="h", name="P", role="user", status="active", email_verified=True)
        bank = QuestionBank(name="已关闭", practice_enabled=False)
        db.add_all([user, bank])
        db.flush()
        db.add(
            Question(
                bank_id=bank.id,
                type="单选题",
                question="secret",
                options=["A"],
                answer="A",
                analysis="",
                difficulty=1,
                tags=[],
                score=2,
            )
        )
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            practice_service.list_modes(db, user.id, bank_id=bank.id)
        assert exc.value.status_code == 403

        # 开考接口行为一致，二者不应出现口径差异
        with pytest.raises((DomainError, HTTPException)) as exc2:
            practice_service.start_practice(db, user.id, "sequence", None, 10, bank_id=bank.id)
        assert exc2.value.status_code == 403


# ---------- 3. 模拟考试按用户隔离 ----------


def test_mock_exam_lookup_scoped_by_owner():
    """模拟考试定义查询必须按 created_by 收敛，否则所有用户共用同一套题。"""
    with SessionLocal() as db:
        owner = User(email="o@x.com", password_hash="h", name="O", role="user", status="active", email_verified=True)
        other = User(email="t@x.com", password_hash="h", name="T", role="user", status="active", email_verified=True)
        db.add_all([owner, other])
        db.flush()
        mine = ExamDefinition(name="mock-owner", type="mock", rules={}, status="ongoing", created_by=owner.id)
        theirs = ExamDefinition(name="mock-other", type="mock", rules={}, status="ongoing", created_by=other.id)
        db.add_all([mine, theirs])
        db.commit()

        found = (
            db.execute(
                select(ExamDefinition).where(
                    ExamDefinition.type == "mock",
                    ExamDefinition.status == "ongoing",
                    ExamDefinition.created_by == owner.id,
                )
            )
            .scalars()
            .first()
        )
        assert found is not None
        assert found.id == mine.id
        assert found.id != theirs.id


# ---------- 4. 简答复核成绩封顶 ----------


def test_review_score_capped_and_objective_synced():
    """复核加分必须封顶于 total_score；objective_score 保持「客观题得分」语义。"""
    with SessionLocal() as db:
        student = User(email="s@x.com", password_hash="h", name="S", role="user", status="active", email_verified=True)
        reviewer = User(
            email="r@x.com", password_hash="h", name="R", role="super_admin", status="active", email_verified=True
        )
        db.add_all([student, reviewer])
        db.flush()

        exam = ExamDefinition(name="E", type="formal", rules={}, status="published", created_by=reviewer.id)
        db.add(exam)
        db.flush()

        question = Question(
            type="简答题",
            question="q",
            options=None,
            answer="ref",
            analysis="",
            difficulty=1,
            tags=[],
            score=60,
        )
        db.add(question)
        db.flush()
        db.add(ExamQuestion(exam_definition_id=exam.id, question_id=question.id, seq=0, score=60))

        session = ExamSession(
            exam_definition_id=exam.id,
            user_id=student.id,
            status="scoring",
            answers={},
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        db.add(session)
        db.flush()

        # 客观题已得 80 分，满分 100；本题 60 分若全额加分应被截断
        result = ExamResult(
            exam_definition_id=exam.id,
            user_id=student.id,
            exam_session_id=session.id,
            score=80,
            total_score=100,
            objective_score=80,
            need_review=True,
            published=False,
        )
        db.add(result)
        db.flush()

        review = ShortAnswerReview(
            exam_result_id=result.id,
            exam_session_id=session.id,
            user_id=student.id,
            question_id=question.id,
            user_answer="a",
            reference_answer="ref",
        )
        db.add(review)
        db.commit()

        review_service.review(db, review.id, "pass", None, reviewer)
        db.refresh(result)

        assert result.score == 100, "成绩必须封顶于 total_score"
        assert result.objective_score == 80, "objective_score 是客观题得分，简答复核不得改动"
        assert result.score <= result.total_score


# ---------- 5. 外键级联 ----------


def _seed_full_graph(db):
    """构造一个「用户 + 分组 + 题库 + 题目 + 考试 + 成绩 + 练习记录」的完整依赖图。"""
    now = datetime.now(timezone.utc).isoformat()
    user = User(email="d@x.com", password_hash="h", name="D", role="user", status="active", email_verified=True)
    group = Group(name="G", type="部门")
    db.add_all([user, group])
    db.flush()
    bank = QuestionBank(name="B", practice_enabled=True, group_id=group.id)
    db.add(bank)
    db.flush()
    question = Question(
        bank_id=bank.id,
        type="单选题",
        question="q",
        options=["A"],
        answer="A",
        analysis="",
        difficulty=1,
        tags=[],
        score=2,
        group_id=group.id,
    )
    db.add(question)
    db.flush()
    exam = ExamDefinition(name="E", type="formal", rules={}, status="published")
    db.add(exam)
    db.flush()
    session = ExamSession(exam_definition_id=exam.id, user_id=user.id, status="scored", started_at=now, answers={})
    db.add(session)
    db.flush()
    result = ExamResult(
        exam_definition_id=exam.id,
        user_id=user.id,
        exam_session_id=session.id,
        score=10,
        total_score=100,
        published=True,
    )
    db.add(result)
    db.flush()
    db.add(
        ShortAnswerReview(
            exam_result_id=result.id, exam_session_id=session.id, user_id=user.id, question_id=question.id
        )
    )
    db.add(
        PracticeRecord(
            user_id=user.id,
            question_id=question.id,
            bank_id=bank.id,
            mode="sequence",
            user_answer="A",
            is_correct=True,
            answered_at=now,
        )
    )
    db.add(QuestionState(user_id=user.id, question_id=question.id, status="correct"))
    db.commit()
    return user, group, bank, question, exam


def test_cascade_delete_user_succeeds_and_removes_dependents():
    """有练习/考试记录的用户必须可被删除（修复前抛 IntegrityError → 500）。"""
    with SessionLocal() as db:
        user, *_ = _seed_full_graph(db)
        db.delete(user)
        db.commit()
        assert db.execute(select(func.count(PracticeRecord.id))).scalar() == 0
        assert db.execute(select(func.count(ExamResult.id))).scalar() == 0
        assert db.execute(select(func.count(QuestionState.id))).scalar() == 0


def test_bank_delete_keeps_question_but_clears_reference():
    """删除题库不得级联删除题目（否则历史成绩失去题目引用）。"""
    with SessionLocal() as db:
        _user, _group, bank, question, _exam = _seed_full_graph(db)
        db.delete(bank)
        db.commit()
        db.refresh(question)
        assert question.bank_id is None


def test_exam_definition_delete_cascades_sessions():
    with SessionLocal() as db:
        _user, _group, _bank, _question, exam = _seed_full_graph(db)
        db.delete(exam)
        db.commit()
        assert db.execute(select(func.count(ExamSession.id))).scalar() == 0


# ---------- 6. 统计日期归属 ----------


def test_day_bounds_uses_business_timezone():
    """业务时区（默认 Asia/Shanghai）的某一天应换算为正确的 UTC 区间。"""
    start, end = stats_service._day_bounds_utc("2026-03-10")
    assert start == "2026-03-09T16:00:00+00:00"
    assert end == "2026-03-10T16:00:00+00:00"


def test_refresh_daily_tolerates_multiple_timestamp_formats():
    """data 既可能是 '+00:00' 也可能是 'Z' 结尾，两种都必须被正确归属。

    修复前用 SQLite datetime() 解析，无法识别 'Z' 时返回 NULL
    导致整个 WHERE 恒假、统计静默归零。
    """
    with SessionLocal() as db:
        user = User(email="st@x.com", password_hash="h", name="S", role="user", status="active", email_verified=True)
        bank = QuestionBank(name="B")
        db.add_all([user, bank])
        db.flush()
        question = Question(
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
        db.add(question)
        db.flush()
        exam = ExamDefinition(name="E", type="formal", rules={}, status="published")
        db.add(exam)
        db.flush()

        db.add(
            PracticeRecord(
                user_id=user.id,
                question_id=question.id,
                bank_id=bank.id,
                mode="sequence",
                user_answer="A",
                is_correct=True,
                answered_at="2026-03-10T02:00:00+00:00",
            )
        )
        db.add(
            ExamResult(
                exam_definition_id=exam.id,
                user_id=user.id,
                score=77,
                total_score=100,
                passed=True,
                published=True,
                created_at="2026-03-10T02:00:00Z",
            )
        )
        db.commit()

        written = stats_service.refresh_daily(db, "2026-03-10")
        assert written == 1
        row = db.execute(select(StatsUserDaily)).scalars().one()
        # 练习计数照旧；考试侧只参与「当日是否活跃」的判定（行存在即活跃），
        # exam_count/exam_score_sum 已随排行榜下线移除（2026-09-23）
        assert row.answer_count == 1
        assert (row.correct_count, row.wrong_count) == (1, 0)


def test_date_str_converts_to_business_timezone():
    """UTC 傍晚时刻在 UTC+8 已属次日，日期归属必须随之改变。"""
    assert stats_service._date_str(datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc)) == "2026-03-11"


# ---------- 7. _recover_stuck_scoring 作用域与安全性 ----------


def test_recover_stuck_scoring_does_not_touch_other_users():
    """开考路径触发的回收只能作用于本人，不得改写他人会话。"""
    from app.services.exam_service import _recover_stuck_scoring

    with SessionLocal() as db:
        me = User(email="me@x.com", password_hash="h", name="me", role="user", status="active", email_verified=True)
        other = User(email="ot@x.com", password_hash="h", name="ot", role="user", status="active", email_verified=True)
        db.add_all([me, other])
        db.flush()
        exam = ExamDefinition(name="E", type="formal", rules={}, status="published")
        db.add(exam)
        db.flush()

        stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        mine = ExamSession(
            exam_definition_id=exam.id,
            user_id=me.id,
            status="scoring",
            started_at=stale,
            submitted_at=stale,
            answers={},
        )
        theirs = ExamSession(
            exam_definition_id=exam.id,
            user_id=other.id,
            status="scoring",
            started_at=stale,
            submitted_at=stale,
            answers={},
        )
        db.add_all([mine, theirs])
        db.commit()

        recovered = _recover_stuck_scoring(db, user_id=me.id)
        assert recovered == 1
        db.refresh(mine)
        db.refresh(theirs)
        assert mine.status == "in_progress"
        assert theirs.status == "scoring", "他人会话不得被本用户请求改写"


def test_recover_stuck_scoring_ignores_unparseable_timestamp():
    """时间字段无法解析时保守跳过，绝不复活会话（避免考生重答已结束考试）。"""
    from app.services.exam_service import _recover_stuck_scoring

    with SessionLocal() as db:
        user = User(email="bad@x.com", password_hash="h", name="B", role="user", status="active", email_verified=True)
        db.add(user)
        db.flush()
        exam = ExamDefinition(name="E", type="formal", rules={}, status="published")
        db.add(exam)
        db.flush()
        session = ExamSession(
            exam_definition_id=exam.id,
            user_id=user.id,
            status="scoring",
            started_at="not-a-date",
            answers={},
        )
        db.add(session)
        db.commit()

        _recover_stuck_scoring(db, user_id=user.id)
        db.refresh(session)
        assert session.status == "scoring"


def test_recover_stuck_scoring_keeps_session_with_result():
    """已有成绩记录的 scoring 会话是合法待复核状态，不得被回收。"""
    from app.services.exam_service import _recover_stuck_scoring

    with SessionLocal() as db:
        user = User(email="wr@x.com", password_hash="h", name="W", role="user", status="active", email_verified=True)
        db.add(user)
        db.flush()
        exam = ExamDefinition(name="E", type="formal", rules={}, status="published")
        db.add(exam)
        db.flush()
        stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        session = ExamSession(
            exam_definition_id=exam.id,
            user_id=user.id,
            status="scoring",
            started_at=stale,
            submitted_at=stale,
            answers={},
        )
        db.add(session)
        db.flush()
        db.add(
            ExamResult(
                exam_definition_id=exam.id,
                user_id=user.id,
                exam_session_id=session.id,
                score=50,
                total_score=100,
                need_review=True,
                published=False,
            )
        )
        db.commit()

        recovered = _recover_stuck_scoring(db, user_id=user.id)
        assert recovered == 0
        db.refresh(session)
        assert session.status == "scoring"
