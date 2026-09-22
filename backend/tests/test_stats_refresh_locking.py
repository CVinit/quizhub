"""`stats.aggregate.refresh_daily` 的并发安全回归测试。

背景：`refresh_daily` 是「读快照 → 全量 DELETE → 重建」。pysqlite 默认只在 DML 前
BEGIN，SELECT 走自动提交；若并发的 `refresh_user_daily`（每次作答/自评/复核后调用）
在读快照之后、DELETE 之前提交当日新行，这些新行会被抹掉并用旧快照重建（丢失更新）。
修复方式是读快照前先 `BEGIN IMMEDIATE` 取写锁。
"""

from __future__ import annotations

from sqlalchemy import event

from app.database import db_session, engine
from app.services import stats_service


def test_refresh_daily_takes_immediate_write_lock():
    """refresh_daily 必须在读快照前取得写锁，否则存在丢失更新窗口。"""
    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _params, _context, _executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        with db_session() as db:
            stats_service.refresh_daily(db, "2026-01-01")
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert any("BEGIN IMMEDIATE" in sql.upper() for sql in statements), (
        f"未取得写锁，丢失更新窗口仍存在；已执行语句：{statements[:5]}"
    )


def test_refresh_daily_preserves_counts_roundtrip():
    """加锁不改变功能：作答后刷新，聚合应与源记录一致。"""
    from sqlalchemy import select

    from app.core.timeutil import utcnow_iso
    from app.models.question import Question
    from app.models.record import PracticeRecord
    from app.models.stats import StatsUserDaily
    from app.models.user import User
    from app.services.stats.common import _date_str, _utcnow

    now = utcnow_iso()
    date_str = _date_str(_utcnow())
    with db_session() as db:
        user = User(email="stats@example.com", password_hash="h", name="u", role="user", status="active")
        question = Question(type="单选题", question="q", answer="A")
        db.add_all([user, question])
        db.flush()
        db.add(
            PracticeRecord(
                user_id=user.id,
                question_id=question.id,
                mode="sequence",
                user_answer="A",
                is_correct=True,
                answered_at=now,
            )
        )
        db.commit()

        stats_service.refresh_daily(db, date_str)
        row = db.execute(
            select(StatsUserDaily).where(StatsUserDaily.user_id == user.id, StatsUserDaily.date == date_str)
        ).scalar_one()
        assert (row.answer_count, row.correct_count, row.wrong_count) == (1, 1, 0)
