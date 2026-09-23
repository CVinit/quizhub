"""2026-09-23 产品决策落地的回归测试。

1. 模拟考试定义收敛：保留最近 N 个「未提交」定义（N 由系统设置 `mock_keep_definitions`
   配置），清理更早的废弃定义连同其未完成会话；**已交卷的模拟成绩不受影响**。
2. 排行榜整体下线：`rank_visible` 设置项被移除，不再接受写入。
3. 迁移脚本清理 settings 表中 `rank_visible` 的遗留行（幂等）。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.database import db_session, init_db
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult, ExamSession
from app.models.user import User
from app.services import exam_service, system_service

_MIGRATION = Path(__file__).resolve().parent.parent / "scripts" / "migrate_2026_09_23.py"


def _mk_user(db, email: str = "mock@example.com") -> User:
    user = User(email=email, password_hash="x", name="u", role="user", status="active", email_verified=True)
    db.add(user)
    db.flush()
    return user


def _mk_bank(db, n: int = 30) -> QuestionBank:
    bank = QuestionBank(name="模拟库", practice_enabled=True)
    db.add(bank)
    db.flush()
    for i in range(n):
        db.add(
            Question(
                bank_id=bank.id,
                type="单选题",
                question=f"q{i}",
                options=["A", "B"],
                answer="A",
                analysis="",
                difficulty=2,
                score=2.0,
            )
        )
    db.flush()
    return bank


def _ongoing_mock_defs(db, user_id: int) -> list[ExamDefinition]:
    return list(
        db.execute(
            select(ExamDefinition).where(
                ExamDefinition.type == "mock",
                ExamDefinition.created_by == user_id,
                ExamDefinition.status == "ongoing",
            )
        )
        .scalars()
        .all()
    )


# ---------- 1. 模拟考试定义收敛 ----------
def test_mock_definitions_are_cleaned_to_configured_keep_limit():
    """5 次不同题量开考 + keep=2 → 只留最近 2 个定义，废弃会话与固化题目一并清理。"""
    init_db()
    with db_session() as db:
        system_service.update_settings(db, "exam", {"mock_keep_definitions": "2"})
        bank = _mk_bank(db)
        user = _mk_user(db)
        db.commit()

        for size in range(1, 6):
            exam_service.start_mock_exam(db, user, bank_ids=[bank.id], size=size)

        assert len(_ongoing_mock_defs(db, user.id)) == 2
        # 每个定义对应一个会话：被清理定义的未完成会话也应删除
        assert db.execute(select(func.count()).select_from(ExamSession)).scalar_one() == 2
        # 保留的定义仍有固化题目
        assert db.execute(select(func.count()).select_from(ExamQuestion)).scalar_one() > 0


def test_mock_cleanup_preserves_submitted_attempt():
    """已交卷的模拟成绩不得被清理连带删除（keep=1 时仍保留该定义与其成绩）。"""
    init_db()
    with db_session() as db:
        system_service.update_settings(db, "exam", {"mock_keep_definitions": "1"})
        bank = _mk_bank(db)
        user = _mk_user(db)
        db.commit()

        started = exam_service.start_mock_exam(db, user, bank_ids=[bank.id], size=1, show_analysis=False)
        sid = started["session_id"]
        exam_service.submit_answer(db, user, sid, started["questions"][0]["id"], "A", started["version"])
        exam_service.submit_exam(db, user, sid)
        submitted_def_id = db.get(ExamSession, sid).exam_definition_id
        result_id = db.execute(select(ExamResult.id).where(ExamResult.exam_session_id == sid)).scalar_one()

        for size in (2, 3, 4):
            exam_service.start_mock_exam(db, user, bank_ids=[bank.id], size=size)

        assert db.get(ExamDefinition, submitted_def_id) is not None
        assert db.get(ExamResult, result_id) is not None
        # keep=1 → 1 个未提交定义 + 1 个已交卷定义
        assert len(_ongoing_mock_defs(db, user.id)) == 2


def test_mock_keep_setting_is_validated():
    """保留数必须是 1~100 的正整数（消费方按 int() 解析）。"""
    init_db()
    with db_session() as db:
        system_service.update_settings(db, "exam", {"mock_keep_definitions": "2"})
        assert system_service.get_settings(db, "exam")["mock_keep_definitions"] == "2"
        for bad in ("0", "-1", "abc", "2.5", "101"):
            with pytest.raises(ValueError):
                system_service.update_settings(db, "exam", {"mock_keep_definitions": bad})


# ---------- 2. 排行榜下线 ----------
def test_rank_setting_is_gone():
    """rank_visible 已随排行榜下线：不再接受写入（此前是合法布尔设置项）。"""
    init_db()
    with db_session() as db, pytest.raises(ValueError):
        system_service.update_settings(db, "general", {"rank_visible": "false"})


# ---------- 3. 迁移脚本 ----------
def _load_migration():
    spec = importlib.util.spec_from_file_location("migrate_2026_09_23", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_2026_09_23"] = module
    spec.loader.exec_module(module)
    return module


def test_migration_removes_legacy_rank_setting(tmp_path):
    module = _load_migration()
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, key TEXT, value TEXT)")
    conn.execute("INSERT INTO settings (key, value) VALUES ('rank_visible', 'true')")
    conn.execute("INSERT INTO settings (key, value) VALUES ('site_name', 'x')")
    conn.commit()

    assert module.count_legacy_rows(conn) == 1
    assert module.remove_legacy_rank_setting(conn) == 1
    conn.commit()
    # 幂等：再跑一次不报错、不误删其它设置
    assert module.remove_legacy_rank_setting(conn) == 0
    assert [row[0] for row in conn.execute("SELECT key FROM settings").fetchall()] == ["site_name"]
    conn.close()
