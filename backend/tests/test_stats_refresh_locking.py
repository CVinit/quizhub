"""统计刷新（`refresh_daily` / `refresh_user_daily`）的并发安全回归测试。

背景：两个函数都是「读快照 → 全量 DELETE → 重建」。pysqlite 默认只在 DML 前 BEGIN，
SELECT 走自动提交；若并发写入在快照之后、DELETE 之前提交，这些新行会被 DELETE 抹掉并用
旧快照重建（丢失更新）。修复方式是读快照前先取写锁（`BEGIN IMMEDIATE`）。

这里断言的是**锁的行为效果**（刷新期间并发写入被阻塞），而不是「捕获到的 SQL 文本里有没有
BEGIN IMMEDIATE」——后者把测试绑死在具体实现上：换个加锁方式（`with_for_update`、换数据库）
就会误报，也复现不出真正的丢失更新窗口。
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
from collections.abc import Callable

import pytest
from sqlalchemy import event, select

from app.config import DB_PATH
from app.core.timeutil import utcnow_iso
from app.database import db_session, engine
from app.models.question import Question
from app.models.record import PracticeRecord
from app.models.stats import StatsUserDaily
from app.models.user import User
from app.services import stats_service
from app.services.stats.common import _date_str, _utcnow


def _seed_practice_row() -> tuple[str, int]:
    """建一个用户 + 题目 + 当日作答记录，返回 (业务日, user_id)。"""
    date_str = _date_str(_utcnow())
    with db_session() as db:
        user = User(
            email=f"lock{secrets.token_hex(3)}@example.com",
            password_hash="h",
            name="u",
            role="user",
            status="active",
        )
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
                answered_at=utcnow_iso(),
            )
        )
        db.flush()
        return date_str, user.id


def _concurrent_write_blocked() -> bool:
    """用另一条连接尝试写入：写锁被持有时应在 busy_timeout 后抛 database is locked。"""
    conn = sqlite3.connect(str(DB_PATH), timeout=0.2)
    try:
        conn.execute(
            "INSERT INTO practice_records (user_id, question_id, mode, answered_at) "
            "VALUES (1, 1, 'sequence', '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        return False
    except sqlite3.OperationalError as exc:
        return "locked" in str(exc)
    finally:
        conn.close()


@pytest.mark.parametrize("which", ["daily", "user_daily"])
def test_refresh_holds_write_lock_across_rebuild(which):
    """刷新期间（读快照 → DELETE → 重建）必须持有写锁，否则存在丢失更新窗口。"""
    date_str, user_id = _seed_practice_row()
    entered = threading.Event()
    release = threading.Event()

    def _on_statement(_conn, _cursor, statement, _params, _context, _executemany):  # type: ignore[no-untyped-def]
        # 卡在重建阶段的第一步：此时写锁必须已经被持有
        if statement.lstrip().upper().startswith("DELETE FROM STATS_USER_DAILY") and not entered.is_set():
            entered.set()
            release.wait(timeout=15)

    target: Callable[..., int]
    args: tuple
    if which == "daily":
        target, args = stats_service.refresh_daily, (date_str,)
    else:
        target, args = stats_service.refresh_user_daily, (user_id, date_str)

    def _worker() -> None:
        with db_session() as db:
            target(db, *args)

    event.listen(engine, "before_cursor_execute", _on_statement)
    worker = threading.Thread(target=_worker)
    try:
        worker.start()
        assert entered.wait(timeout=15), "未观察到刷新进入重建阶段"
        assert _concurrent_write_blocked(), "刷新期间并发写入未被阻塞 → 未持有写锁（存在丢失更新窗口）"
    finally:
        release.set()
        worker.join(timeout=30)
        event.remove(engine, "before_cursor_execute", _on_statement)

    assert not worker.is_alive()


def test_refresh_daily_preserves_counts_roundtrip():
    """加锁不改变功能：作答后刷新，聚合应与源记录一致。"""
    date_str, user_id = _seed_practice_row()
    with db_session() as db:
        stats_service.refresh_daily(db, date_str)
        row = db.execute(
            select(StatsUserDaily).where(StatsUserDaily.user_id == user_id, StatsUserDaily.date == date_str)
        ).scalar_one()
        assert (row.answer_count, row.correct_count, row.wrong_count) == (1, 1, 0)
