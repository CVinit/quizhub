"""统计预聚合、面板与排行的回归测试。

`stats_service.rank` 此前**完全没有测试覆盖**（覆盖率报告为 0%），
而它是用户端排行榜唯一入口；这里补齐四个维度、两种 scope 与连续天数边界。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from app.database import db_session, init_db
from app.models.exam import ExamDefinition
from app.models.group import Group, UserGroup
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult, PracticeRecord, QuestionState
from app.models.stats import StatsUserDaily
from app.models.user import User
from app.services import stats_service


def _mk_user(db, name: str = "用户", role: str = "user") -> User:
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


def _add_stats(
    db,
    user: User,
    date: str,
    *,
    answer: int = 0,
    correct: int = 0,
    exam_cnt: int = 0,
    score: float = 0.0,
    group_id: int | None = None,
) -> None:
    db.add(
        StatsUserDaily(
            user_id=user.id,
            date=date,
            group_id=group_id,
            answer_count=answer,
            correct_count=correct,
            wrong_count=answer - correct,
            exam_count=exam_cnt,
            exam_score_sum=score,
            exam_pass_count=0,
        )
    )


def _mk_exam(db, *, exam_type: str = "formal", pass_score: float = 60.0) -> ExamDefinition:
    e = ExamDefinition(
        name=f"{exam_type}考试",
        type=exam_type,
        rules={},
        group_ids=None,
        duration_min=60,
        pass_score=pass_score,
        max_attempts=0,
        show_score_immediately=True,
        show_analysis=False,
        need_review=False,
        status="published",
    )
    db.add(e)
    db.flush()
    return e


def _mk_question(db, bank: QuestionBank, text: str = "题") -> Question:
    q = Question(
        bank_id=bank.id,
        type="单选题",
        question=text,
        options=["a", "b"],
        answer="A",
        analysis="",
        difficulty=1,
        tags=[],
        score=2,
    )
    db.add(q)
    db.flush()
    return q


def test_rank_all_dimensions_and_scopes():
    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        high = _mk_user(db, "甲")
        low = _mk_user(db, "乙")
        lone = _mk_user(db, "丙")
        db.add_all([UserGroup(user_id=high.id, group_id=group.id), UserGroup(user_id=low.id, group_id=group.id)])
        today = stats_service._date_str(stats_service._utcnow())
        _add_stats(db, high, today, answer=10, correct=9, exam_cnt=1, score=80.0, group_id=group.id)
        _add_stats(db, low, today, answer=10, correct=5, exam_cnt=2, score=100.0, group_id=group.id)
        _add_stats(db, lone, today, answer=4, correct=1)
        db.commit()

        by_accuracy = stats_service.rank(db, "accuracy", "self", "7d", high.id)
        assert by_accuracy[0]["user_id"] == high.id
        assert by_accuracy[0]["value"] == 90
        assert by_accuracy[0]["is_me"] is True
        assert by_accuracy[0]["name"] == "甲"

        by_count = stats_service.rank(db, "count", "self", "7d")
        assert by_count[0]["value"] == 10
        assert by_count[0]["answer_count"] == 10

        # score 维度是「平均分」：甲 80/1=80，乙 100/2=50
        by_score = stats_service.rank(db, "score", "self", "30d")
        assert by_score[0]["user_id"] == high.id

        by_streak = stats_service.rank(db, "streak", "self", "all")
        assert all(item["value"] >= 1 for item in by_streak)

        # 未知维度不抛错，退化为 0
        assert stats_service.rank(db, "unknown", "self", "7d")[0]["value"] == 0

        grouped = stats_service.rank(db, "count", "group", "7d")
        names = {item["name"] for item in grouped}
        assert "研发部" in names
        assert "未分组" in names


def test_rank_top10_and_empty_range():
    init_db()
    with db_session() as db:
        today = stats_service._date_str(stats_service._utcnow())
        for i in range(12):
            user = _mk_user(db, f"用户{i}")
            _add_stats(db, user, today, answer=i + 1, correct=i + 1)
        db.commit()

        top = stats_service.rank(db, "count", "self", "7d")
        assert len(top) == 10  # 只返回 Top10

        # 范围外（很久以前）不应命中 7d
        old = _mk_user(db, "古人")
        _add_stats(db, old, "2000-01-01", answer=99, correct=99)
        db.commit()
        assert all(item["user_id"] != old.id for item in stats_service.rank(db, "count", "self", "7d"))
        assert any(item["user_id"] == old.id for item in stats_service.rank(db, "count", "self", "all"))


def test_streak_boundaries():
    init_db()
    with db_session() as db:
        today = datetime.now(timezone.utc)
        dates = [stats_service._date_str(today - timedelta(days=i)) for i in range(3)]

        consecutive = _mk_user(db, "连击")
        for d in dates:
            _add_stats(db, consecutive, d, answer=1, correct=1)

        broken = _mk_user(db, "中断")
        _add_stats(db, broken, stats_service._date_str(today - timedelta(days=5)), answer=1, correct=1)

        yesterday_only = _mk_user(db, "仅昨日")
        _add_stats(db, yesterday_only, dates[1], answer=1, correct=1)
        db.commit()

        # 直接测批量实现（原 `_streak` 是只被测试引用的生产死代码，已删除）
        assert stats_service._streaks(db, [consecutive.id], "0000-00-00").get(consecutive.id, 0) == 3
        assert stats_service._streaks(db, [broken.id], "0000-00-00").get(broken.id, 0) == 0
        # 今日未答但昨日已答 → 从昨日起继续计数
        assert stats_service._streaks(db, [yesterday_only.id], "0000-00-00").get(yesterday_only.id, 0) == 1

        nobody = _mk_user(db, "无记录")
        db.commit()
        assert stats_service._streaks(db, [nobody.id], "0000-00-00").get(nobody.id, 0) == 0


def test_refresh_daily_aggregates_and_is_idempotent():
    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add_all([group, bank])
        db.flush()
        user = _mk_user(db, "甲")
        db.add(UserGroup(user_id=user.id, group_id=group.id))
        q = _mk_question(db, bank)
        now = datetime.now(timezone.utc).isoformat()
        db.add(
            PracticeRecord(
                user_id=user.id,
                question_id=q.id,
                bank_id=bank.id,
                mode="sequence",
                user_answer={"a": 1},
                is_correct=True,
                answered_at=now,
            )
        )
        exam = _mk_exam(db)
        db.add(
            ExamResult(
                exam_definition_id=exam.id,
                user_id=user.id,
                score=80,
                total_score=100,
                passed=True,
                need_review=False,
                published=True,
                created_at=now,
            )
        )
        db.commit()

        date_str = stats_service._date_str(datetime.now(timezone.utc))
        assert stats_service.refresh_daily(db, date_str) == 1

        row = db.execute(select(StatsUserDaily).where(StatsUserDaily.date == date_str)).scalar_one()
        assert row.user_id == user.id
        assert row.answer_count == 1
        assert row.correct_count == 1
        assert row.exam_count == 1
        assert row.exam_score_sum == 80
        assert row.group_id == group.id

        # 幂等：重刷不产生重复行
        assert stats_service.refresh_daily(db, date_str) == 1
        rows_after = db.execute(select(StatsUserDaily).where(StatsUserDaily.date == date_str)).scalars().all()
        assert len(rows_after) == 1

        # 源数据清空后重刷 → 聚合行也清空（不残留旧结果）
        db.execute(delete(PracticeRecord))
        db.execute(delete(ExamResult))
        db.commit()
        assert stats_service.refresh_daily(db, date_str) == 0
        assert (
            db.execute(
                select(func.count()).select_from(StatsUserDaily).where(StatsUserDaily.date == date_str)
            ).scalar_one()
            == 0
        )

        assert stats_service.refresh_recent(db, 2) >= 0


def test_admin_overview_full_scoped_and_empty_scope():
    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        other = Group(name="市场部", type="部门")
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add_all([group, other, bank])
        db.flush()

        mine = _mk_user(db, "本部门")
        theirs = _mk_user(db, "其他部门")
        pending = _mk_user(db, "待审批")
        pending.status = "pending"
        db.add_all([UserGroup(user_id=mine.id, group_id=group.id), UserGroup(user_id=theirs.id, group_id=other.id)])

        q = _mk_question(db, bank)
        db.add(QuestionState(user_id=mine.id, question_id=q.id, status="correct", marked=False, marked_note=""))
        db.add(
            PracticeRecord(
                user_id=mine.id,
                question_id=q.id,
                bank_id=bank.id,
                mode="sequence",
                user_answer=None,
                is_correct=True,
                answered_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        db.add(
            PracticeRecord(
                user_id=theirs.id,
                question_id=q.id,
                bank_id=bank.id,
                mode="sequence",
                user_answer=None,
                is_correct=False,
                answered_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        scoped_exam = _mk_exam(db)
        scoped_exam.group_ids = [group.id]
        db.commit()

        full = stats_service.admin_overview(db, None)
        assert full["total_users"] == 3
        assert full["total_questions"] == 1
        assert full["total_exams"] == 1

        scoped = stats_service.admin_overview(db, {group.id})
        assert scoped["total_users"] == 1
        assert scoped["pending_approvals"] == 0
        # 组卷范围外的考试不计入
        assert scoped["total_exams"] == 1
        assert scoped["total_questions"] == 1

        empty = stats_service.admin_overview(db, set())
        assert empty["total_users"] == 0
        assert empty["pending_reviews"] == 0
        assert empty["today_active"] == 0


def test_user_panel_counts_states_and_mock_average():
    init_db()
    with db_session() as db:
        bank = QuestionBank(name="开放库", practice_enabled=True)
        closed = QuestionBank(name="仅考试库", practice_enabled=False)
        db.add_all([bank, closed])
        db.flush()
        user = _mk_user(db, "甲")
        q1 = _mk_question(db, bank, "题1")
        q2 = _mk_question(db, bank, "题2")
        hidden = _mk_question(db, closed, "关闭库题")
        db.add_all(
            [
                QuestionState(user_id=user.id, question_id=q1.id, status="correct", marked=False, marked_note=""),
                QuestionState(user_id=user.id, question_id=q2.id, status="wrong", marked=True, marked_note="看这题"),
                QuestionState(user_id=user.id, question_id=hidden.id, status="correct", marked=False, marked_note=""),
            ]
        )
        mock = _mk_exam(db, exam_type="mock", pass_score=0)
        formal = _mk_exam(db, exam_type="formal")
        db.add_all(
            [
                ExamResult(
                    exam_definition_id=mock.id,
                    user_id=user.id,
                    score=8,
                    total_score=10,
                    passed=True,
                    need_review=False,
                    published=True,
                ),
                ExamResult(
                    exam_definition_id=mock.id,
                    user_id=user.id,
                    score=10,
                    total_score=10,
                    passed=True,
                    need_review=False,
                    published=False,  # 未公布不计入模拟平均分
                ),
                ExamResult(
                    exam_definition_id=formal.id,
                    user_id=user.id,
                    score=50,
                    total_score=100,
                    passed=False,
                    need_review=False,
                    published=True,
                ),
            ]
        )
        db.commit()

        panel = stats_service.user_panel(db, user)
        # 仅统计开放练习题库：2 题，且状态统计同样排除关闭题库那一题
        assert panel["total"] == 2
        assert panel["practiced"] == 2
        assert panel["correct"] == 1
        assert panel["wrong"] == 1
        assert panel["marked"] == 1
        assert panel["accuracy"] == 50
        # 仅已发布的模拟考试：8/10 → 80.0 分
        assert panel["mock_avg_score"] == 80.0
        assert panel["mock_attempts"] == 1
        assert {e["type"] for e in panel["recent_exams"]} == {"mock", "formal"}


def test_rank_query_count_does_not_grow_with_users():
    """回归：rank 曾对每个上榜用户各查一次（streak 再加一次），即 O(用户数) 次查询。

    重构后查询数应固定为常数（聚合 + 用户名 + 分组 + 连续天数），与用户数无关。
    """
    from sqlalchemy import event

    from app.database import engine

    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        today = stats_service._date_str(stats_service._utcnow())
        for i in range(12):
            user = _mk_user(db, f"用户{i}")
            db.add(UserGroup(user_id=user.id, group_id=group.id))
            _add_stats(db, user, today, answer=i + 1, correct=i + 1, group_id=group.id)
        db.commit()

        statements: list[str] = []

        def _record(_conn, _cursor, statement, _params, _context, _executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _record)
        try:
            items = stats_service.rank(db, "streak", "self", "7d")
            group_items = stats_service.rank(db, "count", "group", "7d")
        finally:
            event.remove(engine, "before_cursor_execute", _record)

        assert len(items) == 10
        assert group_items
        # 两次 rank 各 3~4 条查询；若退回 N+1，12 个用户会产生 24+ 条
        assert len(statements) <= 8, f"查询数随用户数增长，疑似 N+1：{len(statements)}"
