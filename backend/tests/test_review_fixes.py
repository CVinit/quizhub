"""2026-09 全面后端审查的修复回归测试。

覆盖 8 个 Critical 修复中的可自动化断言项：
1. 标签筛选不得丢弃多标签题目（JSON .contains 是子串匹配）；
2. 系统设置解密遇到 InvalidToken（密钥轮换）应降级而非 500；
3. 最后一个超级管理员不得被降级/禁用（防权限锁死）；
4. 练习/交卷写路径应即时刷新本人当日统计聚合；
5. 删除被 dept_group_id 引用的分组不得触发外键失败；
6. 迁移重建 exam_sessions 后必须补回 uq_active_exam_session 部分唯一索引；
7. 邮件信封地址必须清洗 CR/LF，且 ValueError 应包装为 MailError。
"""

from __future__ import annotations

import importlib.util
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.errors import DomainError
from app.database import db_session, init_db
from app.models.group import Group
from app.models.question import Question, QuestionBank
from app.models.stats import StatsUserDaily
from app.models.system import Setting
from app.models.user import User
from app.schemas.group import GroupCreate
from app.services import group_service, mail_service, user_service
from app.services.paper_service import generate_paper
from app.services.question_service import type_stats
from app.services.system_service import get_settings

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _mk_user(db, role: str = "user", *, dept_group_id: int | None = None) -> User:
    u = User(
        email=f"{secrets.token_hex(4)}@example.com",
        password_hash="x",
        name="用户",
        role=role,
        status="active",
        email_verified=True,
        dept_group_id=dept_group_id,
    )
    db.add(u)
    db.flush()
    return u


def _mk_bank(db, name: str = "库", *, enabled: bool = True) -> QuestionBank:
    b = QuestionBank(name=name, practice_enabled=enabled)
    db.add(b)
    db.flush()
    return b


def _mk_question(db, bank: QuestionBank, *, tags: list[str] | None = None, answer: str = "A") -> Question:
    q = Question(
        bank_id=bank.id,
        type="单选题",
        question="题",
        options=["A", "B"],
        answer=answer,
        analysis="",
        difficulty=1,
        tags=tags or [],
        score=2,
    )
    db.add(q)
    db.flush()
    return q


# ---------- 1. 标签筛选 ----------
def test_generate_paper_tag_filter_keeps_multitag_questions():
    """回归：带多个标签的题目也必须被标签筛选命中。"""
    init_db()
    with db_session() as db:
        bank = _mk_bank(db, "标签库")
        for tags in (["网络"], ["网络", "安全"], ["安全"], ["网络", "安全", "运维"]):
            db.add(_mk_question(db, bank, tags=tags, answer="A"))
        db.commit()
        # 让题目文本可区分，便于排序断言（此处只关心数量）
        assert db.query(Question).count() == 4

        paper = generate_paper(db, {"type_quota": {"单选题": 10}, "tags": ["网络"]})
        assert paper["count"] == 3, "多标签题目被标签筛选误删"

        # 组卷来源筛选与题型统计必须同口径
        assert type_stats(db, tags=["网络"])["单选题"] == 3
        assert type_stats(db, tags=["安全"])["单选题"] == 3


# ---------- 2. 设置解密降级 ----------
def _mk_setting(key: str, value: str, category: str, encrypted: bool) -> Setting:
    return Setting(setting_key=key, value=value, category=category, encrypted=encrypted)


def test_get_settings_survives_ciphertext_from_rotated_key(monkeypatch):
    """密钥轮换/密文损坏抛 InvalidToken 时应降级为空值，而不是让接口 500。"""
    from cryptography.fernet import Fernet

    import app.core.security as security

    writer = Fernet(Fernet.generate_key())
    reader = Fernet(Fernet.generate_key())
    monkeypatch.setattr(security, "_fernet", lambda: reader)

    init_db()
    with db_session() as db:
        db.add(_mk_setting("smtp_host", "smtp.example.com", "smtp", False))
        db.add(_mk_setting("smtp_password", "enc:" + writer.encrypt(b"s3cret").decode(), "smtp", True))
        db.commit()

        settings = get_settings(db, "smtp")
        assert settings["smtp_host"] == "smtp.example.com"
        assert settings["smtp_password"] == ""


def test_get_settings_plain_prefix_without_flag_degrades():
    """遗留 plain: 值即使 encrypted 标记为 0 也应降级，不能当明文口令使用。"""
    init_db()
    with db_session() as db:
        db.add(_mk_setting("smtp_password", "plain:xxxx", "smtp", False))
        db.commit()
        assert get_settings(db, "smtp")["smtp_password"] == ""


# ---------- 3. 最后一个超管保护 ----------
def test_last_super_admin_cannot_demote_or_disable_self():
    init_db()
    with db_session() as db:
        s = _mk_user(db, "super_admin")
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as disable:
            user_service.set_status(db, s.id, s.id, False, None)
        assert disable.value.status_code == 400

        with pytest.raises((DomainError, HTTPException)) as demote:
            user_service.update_user(db, s.id, s.id, None, "user", None, None)
        assert demote.value.status_code == 400

        db.expire_all()
        assert db.get(User, s.id).role == "super_admin"
        assert db.get(User, s.id).status == "active"


def test_super_admin_operations_allowed_when_another_active_super_exists():
    """还有其它 active 超管时，禁用其中一个/降级其中一个应被允许。"""
    init_db()
    with db_session() as db:
        s1 = _mk_user(db, "super_admin")
        s2 = _mk_user(db, "super_admin")
        db.commit()

        assert user_service.set_status(db, s1.id, s2.id, False, None).status == "disabled"
        # 降级 s2（已被禁用，不是最后一个 active 超管）
        assert user_service.update_user(db, s1.id, s2.id, None, "user", None, None).role == "user"


# ---------- 4. 统计即时刷新 ----------
def test_answer_question_refreshes_daily_stats():
    """练习作答后当日聚合应立即反映，无需等手动刷新或进程重启。"""
    from app.services import practice_service

    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        user = _mk_user(db)
        q = _mk_question(db, bank, answer="A")
        db.commit()

        practice_service.answer_question(db, user.id, q.id, "A", "sequence")

        from app.services import stats_service

        date_str = stats_service._date_str(datetime.now(timezone.utc))
        row = db.execute(
            select(StatsUserDaily).where(StatsUserDaily.user_id == user.id, StatsUserDaily.date == date_str)
        ).scalar_one()
        assert row.answer_count == 1
        assert row.correct_count == 1


def test_submit_exam_refreshes_daily_stats():
    """交卷生成成绩后当日统计应立即包含该考试（正式 + 已公布）。"""
    from app.models.exam import ExamDefinition
    from app.services import exam_service, stats_service

    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        user = _mk_user(db)
        q = _mk_question(db, bank, answer="A")
        exam = ExamDefinition(
            name="正式考试",
            type="formal",
            rules={},
            manual_questions=[q.id],
            group_ids=None,
            duration_min=60,
            pass_score=60,
            max_attempts=0,
            show_score_immediately=True,
            need_review=False,
            status="published",
        )
        db.add(exam)
        db.commit()

        started = exam_service.start_exam(db, user, exam.id)
        sid = started["session_id"]
        exam_service.submit_answer(db, user, sid, q.id, "A", started["version"])
        exam_service.submit_exam(db, user, sid)

        date_str = stats_service._date_str(datetime.now(timezone.utc))
        row = db.execute(
            select(StatsUserDaily).where(StatsUserDaily.user_id == user.id, StatsUserDaily.date == date_str)
        ).scalar_one()
        # 已公布的正式考试算作当日活跃（聚合行存在即活跃）；考试类计数列已随排行榜下线移除
        assert row is not None


def test_mock_exam_result_does_not_enter_daily_stats():
    """回归：模拟考不算当日活跃（口径见 mock-exam-redesign 规格）。"""
    from app.services import exam_service, stats_service

    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        user = _mk_user(db)
        q = _mk_question(db, bank, answer="A")
        db.commit()

        started = exam_service.start_mock_exam(db, user, bank_ids=[bank.id], size=1, show_analysis=False)
        sid = started["session_id"]
        exam_service.submit_answer(db, user, sid, q.id, "A", started["version"])
        res = exam_service.submit_exam(db, user, sid)
        # 模拟考本身即时出分（不影响本断言），但统计口径必须排除它
        assert res["need_review"] is False

        date_str = stats_service._date_str(datetime.now(timezone.utc))
        row = db.execute(
            select(StatsUserDaily).where(StatsUserDaily.user_id == user.id, StatsUserDaily.date == date_str)
        ).scalar_one_or_none()
        assert row is None, "只做了模拟考的用户不应产生当日活跃行"


def test_unpublished_formal_result_does_not_enter_daily_stats():
    """回归：含简答、待复核（published=False）的正式成绩不算当日活跃。"""
    from app.services import exam_service, stats_service

    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        user = _mk_user(db)
        q = _mk_question(db, bank, answer="参考作答")
        q.type = "简答题"
        db.commit()

        from app.models.exam import ExamDefinition

        exam = ExamDefinition(
            name="简答考试",
            type="formal",
            rules={},
            manual_questions=[q.id],
            group_ids=None,
            duration_min=60,
            pass_score=60,
            max_attempts=0,
            show_score_immediately=True,
            need_review=True,
            status="published",
        )
        db.add(exam)
        db.commit()

        started = exam_service.start_exam(db, user, exam.id)
        sid = started["session_id"]
        exam_service.submit_answer(db, user, sid, q.id, "我的作答", started["version"])
        exam_service.submit_exam(db, user, sid)

        date_str = stats_service._date_str(datetime.now(timezone.utc))
        row = db.execute(
            select(StatsUserDaily).where(StatsUserDaily.user_id == user.id, StatsUserDaily.date == date_str)
        ).scalar_one_or_none()
        assert row is None, "未公布成绩不得产生当日活跃行"


# ---------- 5. 删除分组解除部门引用 ----------
def test_group_delete_clears_department_admin_reference():
    """被 dept_group_id 引用的分组必须可删除（模型声明 SET NULL）。"""
    init_db()
    with db_session() as db:
        group = group_service.create_group(db, GroupCreate(name="研发部"))
        admin = _mk_user(db, "dept_admin", dept_group_id=group.id)
        db.commit()

        group_service.delete_group(db, group.id)

        assert db.get(Group, group.id) is None
        db.refresh(admin)
        assert admin.dept_group_id is None


# ---------- 6 & 7. 迁移脚本 ----------
def _load_migration(filename: str):
    script = _SCRIPTS / filename
    spec = importlib.util.spec_from_file_location(filename.replace(".py", ""), script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[filename.replace(".py", "")] = mod
    spec.loader.exec_module(mod)
    return mod


def test_exam_sessions_rebuild_restores_partial_unique_index(tmp_path: Path):
    """重建旧 exam_sessions 后必须补回 uq_active_exam_session。"""
    m = _load_migration("migrate_2026_09_16.py")
    db = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE exam_definitions (id INTEGER PRIMARY KEY)")
    # 旧表：无 ON DELETE 规则 → 会走重建路径
    conn.execute(
        "CREATE TABLE exam_sessions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, exam_definition_id INTEGER NOT NULL,"
        "user_id INTEGER NOT NULL, status VARCHAR NOT NULL, answers JSON NOT NULL,"
        "version INTEGER NOT NULL, started_at VARCHAR NOT NULL, submitted_at VARCHAR,"
        "remaining_sec INTEGER, created_at VARCHAR NOT NULL)"
    )
    conn.execute("INSERT INTO users (id) VALUES (1)")
    conn.execute("INSERT INTO exam_definitions (id) VALUES (1)")
    conn.execute(
        "INSERT INTO exam_sessions (exam_definition_id,user_id,status,answers,version,started_at,created_at) "
        "VALUES (1,1,'in_progress','{}',1,'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
    )
    conn.commit()

    rebuilt = m.rebuild_table_with_ondelete(conn, "exam_sessions", m._TABLE_DDL["exam_sessions"], dry_run=False)

    assert rebuilt is True
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='exam_sessions'")
    }
    assert "uq_active_exam_session" in names, "重建后部分唯一索引丢失"
    assert conn.execute("SELECT COUNT(*) FROM exam_sessions").fetchone()[0] == 1
    conn.close()


def test_purge_orphans_nullifies_dangling_reviewer(tmp_path: Path):
    """悬空的 short_answer_reviews.reviewer 应置空，而不是让 foreign_key_check 中断迁移。"""
    m = _load_migration("migrate_2026_09_16.py")
    db = tmp_path / "orphan.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO users (id) VALUES (1)")
    # purge_orphans 把 exam_sessions/exam_results 也当子表检查，需带上其外键列
    conn.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE exam_definitions (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE exam_sessions (id INTEGER PRIMARY KEY, user_id INTEGER, exam_definition_id INTEGER)")
    conn.execute(
        "CREATE TABLE exam_results ("
        "id INTEGER PRIMARY KEY, user_id INTEGER, exam_definition_id INTEGER, exam_session_id INTEGER)"
    )
    conn.execute(
        "CREATE TABLE short_answer_reviews ("
        "id INTEGER PRIMARY KEY, reviewer INTEGER, user_id INTEGER,"
        "exam_result_id INTEGER, exam_session_id INTEGER, question_id INTEGER)"
    )
    conn.execute("INSERT INTO short_answer_reviews (id, reviewer, user_id) VALUES (1, 999, 1)")
    conn.commit()

    cleaned = m.purge_orphans(conn, dry_run=False)

    assert cleaned == 1
    assert conn.execute("SELECT reviewer FROM short_answer_reviews WHERE id=1").fetchone()[0] is None
    conn.close()


# ---------- 8. 邮件信封清洗 ----------
class _FakeSMTP:
    instances: list[_FakeSMTP] = []

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.calls: list[tuple] = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def starttls(self, context=None):
        self.calls.append(("starttls", context is not None))

    def sendmail(self, sender, to, message):
        self.calls.append(("sendmail", sender, to, message))


def _tls_settings(**overrides) -> dict[str, str]:
    settings = {
        "smtp_host": "smtp.example.com",
        "smtp_port": "465",
        "smtp_username": "u@example.com",
        "smtp_password": "secret",
        "smtp_sender": "sender@example.com",
        "smtp_use_tls": "true",
        "site_name": "培训平台",
    }
    settings.update(overrides)
    return settings


def test_send_sanitizes_envelope_recipient_and_sender(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(mail_service.smtplib, "SMTP_SSL", _FakeSMTP)

    mail_service._send(
        "to@example.com\r\nRCPT TO:<evil@example.com>",
        "主题",
        "正文",
        _tls_settings(smtp_sender="sender@example.com\r\nRCPT TO:<evil2@example.com>"),
    )

    sendmail = [c for c in _FakeSMTP.instances[-1].calls if c[0] == "sendmail"][-1]
    sender, recipients = sendmail[1], sendmail[2]
    assert "\r" not in sender and "\n" not in sender
    assert all("\r" not in r and "\n" not in r for r in recipients)


def test_send_wraps_value_error_as_mail_error(monkeypatch):
    class _BoomSMTP(_FakeSMTP):
        def sendmail(self, sender, to, message):
            raise ValueError("command and arguments contain prohibited newline characters")

    monkeypatch.setattr(mail_service.smtplib, "SMTP_SSL", _BoomSMTP)

    with pytest.raises(mail_service.MailError):
        mail_service._send("to@example.com", "主题", "正文", _tls_settings())
