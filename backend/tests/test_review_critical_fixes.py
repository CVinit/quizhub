"""2026-09-18 全面后端审查 6 个 Critical 修复的回归测试。

覆盖：
1. 及格线为百分制（`grading.is_passed`）：满分小卷能及格、低分不能；
2. 统计聚合只纳入「已公布正式考试成绩」（模拟考/待复核成绩不进榜）；
3. `publish_results` 必须按考生数据范围过滤（部门管理员不得公布范围外成绩）；
4. `remaining_sec` 按服务端真实起点实时计算（断点续考不重置倒计时）；
5. `build_tree` 不因 sort 顺序把同一分组输出两次；
6. 邮件失败日志与 `MailError` 不携带收件人/发件人邮箱（PII）。
"""

from __future__ import annotations

import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import hash_password
from app.database import db_session, init_db
from app.models.exam import ExamDefinition
from app.models.group import Group, UserGroup
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult
from app.models.stats import StatsUserDaily
from app.models.user import User
from app.services import exam_service, group_service, mail_service, review_service, stats_service
from app.services.grading import is_passed

# ---------- 公共构造 ----------


def _mk_user(
    db,
    role: str = "user",
    *,
    email: str | None = None,
    dept_group_id: int | None = None,
) -> User:
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


def _mk_questions(db, bank: QuestionBank, count: int, *, answer: str = "A", score: float = 2.0) -> list[Question]:
    questions = []
    for i in range(count):
        q = Question(
            bank_id=bank.id,
            type="单选题",
            question=f"题{i}",
            options=["甲", "乙"],
            answer=answer,
            analysis="",
            difficulty=1,
            tags=[],
            score=score,
        )
        db.add(q)
        questions.append(q)
    db.flush()
    return questions


# ---------- 1. 及格线百分制 ----------


def test_is_passed_uses_percentage():
    assert is_passed(2.0, 2.0, 60) is True  # 100% >= 60
    assert is_passed(1.0, 2.0, 60) is False  # 50% < 60
    assert is_passed(1.0, 2.0, 40) is True  # 50% >= 40
    assert is_passed(0.0, 0.0, 60) is False  # 空卷
    assert is_passed(2.0, 2.0, 60, overtime=True) is False  # 超时即不及格


def test_small_mock_paper_can_pass_with_default_pass_line():
    """回归：10/20/30 题的卷子满分不足 100，按原始分比较时满分也永远不及格。"""
    init_db()
    with db_session() as db:
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        _mk_questions(db, bank, 3)
        user = _mk_user(db)
        db.commit()

        started = exam_service.start_mock_exam(db, user, bank_ids=[bank.id], size=1, show_analysis=False)
        sid = started["session_id"]
        q = started["questions"][0]
        exam_service.submit_answer(db, user, sid, q["id"], "A", started["version"])
        res = exam_service.submit_exam(db, user, sid)

        # 单题满分 2 分、及格线默认 60（百分制）→ 100% 应判及格
        assert res["score"] == pytest.approx(2.0)
        assert res["total_score"] == pytest.approx(2.0)
        assert res["passed"] is True


def test_formal_exam_below_pass_line_is_failed():
    init_db()
    with db_session() as db:
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        questions = _mk_questions(db, bank, 2)
        user = _mk_user(db)
        exam = ExamDefinition(
            name="正式考试",
            type="formal",
            rules={},
            manual_questions=[q.id for q in questions],
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
        # 只答对第一题 → 2/4 = 50% < 60%
        exam_service.submit_answer(db, user, sid, questions[0].id, "A", started["version"])
        exam_service.submit_answer(db, user, sid, questions[1].id, "B", started["version"] + 1)
        res = exam_service.submit_exam(db, user, sid)

        assert res["score"] == pytest.approx(2.0)
        assert res["passed"] is False

        # 及格线下调为 40% 后重算为及格
        exam_service.update_exam(db, exam.id, {"pass_score": 40}, None)
        db.expire_all()
        result = db.query(ExamResult).filter(ExamResult.exam_definition_id == exam.id).one()
        assert result.passed is True


# ---------- 3. publish_results 数据范围 ----------


def test_publish_results_only_publishes_in_scope_users():
    init_db()
    with db_session() as db:
        own = Group(name="本部门", type="部门")
        other = Group(name="其他部门", type="部门")
        db.add_all([own, other])
        db.flush()

        exam = ExamDefinition(
            name="本部门考试",
            type="formal",
            rules={},
            group_ids=[own.id],
            duration_min=60,
            pass_score=60,
            status="published",
        )
        db.add(exam)
        db.flush()

        in_scope = _mk_user(db)
        out_of_scope = _mk_user(db)
        db.add_all(
            [
                UserGroup(user_id=in_scope.id, group_id=own.id),
                UserGroup(user_id=out_of_scope.id, group_id=other.id),
            ]
        )
        db.add_all(
            [
                ExamResult(
                    exam_definition_id=exam.id,
                    user_id=in_scope.id,
                    score=100,
                    total_score=100,
                    published=False,
                ),
                ExamResult(
                    exam_definition_id=exam.id,
                    user_id=out_of_scope.id,
                    score=100,
                    total_score=100,
                    published=False,
                ),
            ]
        )
        db.commit()

        result = review_service.publish_results(db, exam.id, {own.id}, None)
        assert result["published"] == 1

        db.expire_all()
        rows = {
            row.user_id: row.published
            for row in db.query(ExamResult).filter(ExamResult.exam_definition_id == exam.id).all()
        }
        assert rows[in_scope.id] is True
        assert rows[out_of_scope.id] is False, "范围外考生的成绩不得被部门管理员公布"


# ---------- 4. remaining_sec 实时计算 ----------


def test_remaining_seconds_is_recomputed_from_started_at():
    init_db()
    with db_session() as db:
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        questions = _mk_questions(db, bank, 1)
        user = _mk_user(db)
        exam = ExamDefinition(
            name="正式考试",
            type="formal",
            rules={},
            manual_questions=[questions[0].id],
            group_ids=None,
            duration_min=60,
            pass_score=60,
            status="published",
        )
        db.add(exam)
        db.commit()

        started = exam_service.start_exam(db, user, exam.id)
        sid = started["session_id"]
        # 模拟「已开考 30 分钟」后重新进入（断点续考）
        from app.models.record import ExamSession

        sess = db.get(ExamSession, sid)
        sess.started_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        db.commit()

        detail = exam_service.session_detail(db, user, sid)
        # 时长 60 分钟，已过 30 分钟 → 剩余约 1800 秒，绝不能重置回 3600
        assert 1700 <= detail["remaining_sec"] <= 1800


# ---------- 5. build_tree 不重复输出 ----------


def _flatten(nodes: list[dict]) -> list[int]:
    out: list[int] = []
    stack = list(nodes)
    while stack:
        node = stack.pop()
        out.append(node["id"])
        stack.extend(node["children"])
    return out


def test_build_tree_does_not_duplicate_when_child_sorts_before_parent():
    init_db()
    with db_session() as db:
        parent = Group(name="父", type="部门", sort=10)
        db.add(parent)
        db.flush()
        child = Group(name="子", type="部门", parent_id=parent.id, sort=1)
        db.add(child)
        db.commit()

        tree = group_service.build_tree(db, None)
        assert len(tree) == 1, "子节点 sort 小于父节点时不应被当成根节点重复输出"
        assert tree[0]["id"] == parent.id
        assert sorted(_flatten(tree)) == sorted([parent.id, child.id])

        # 作用域收窄到子节点时，子节点应作为根返回且只出现一次
        scoped = group_service.build_tree(db, {child.id})
        assert [node["id"] for node in scoped] == [child.id]
        assert _flatten(scoped) == [child.id]


# ---------- 6. 邮件日志/异常脱敏 ----------


class _BoomSMTP:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def login(self, *args) -> None:
        pass

    def sendmail(self, sender, recipients, message):
        raise smtplib.SMTPRecipientsRefused({"alice@example.com": (550, b"no such user")})


def _tls_settings() -> dict[str, str]:
    return {
        "smtp_host": "smtp.example.com",
        "smtp_port": "465",
        "smtp_username": "mailer",
        "smtp_password": "secret",
        "smtp_sender": "sender@example.com",
        "smtp_use_tls": "true",
        "site_name": "测试站点",
    }


def test_redact_masks_email_addresses():
    redacted = mail_service._redact("SMTPRecipientsRefused({'alice@example.com': (550, b'no')})")
    assert "alice@example.com" not in redacted
    assert "<redacted>" in redacted


def test_mail_error_and_log_do_not_leak_recipient(monkeypatch, caplog):
    monkeypatch.setattr(mail_service.smtplib, "SMTP_SSL", _BoomSMTP)

    with pytest.raises(mail_service.MailError) as exc:
        mail_service._send("alice@example.com", "主题", "正文", _tls_settings())
    assert "alice@example.com" not in str(exc.value)

    with caplog.at_level(logging.ERROR, logger="quizhub"):
        mail_service.send_safely(mail_service._send, "alice@example.com", "主题", "正文", _tls_settings())
    assert "alice@example.com" not in caplog.text
    assert "邮件发送失败" in caplog.text


# ---------- 2. 统计口径的单元断言（端到端见 test_review_fixes） ----------


def test_refresh_daily_ignores_mock_and_unpublished_results():
    init_db()
    with db_session() as db:
        user = _mk_user(db)
        mock_exam = ExamDefinition(name="模拟", type="mock", rules={}, status="ongoing")
        formal_exam = ExamDefinition(name="正式", type="formal", rules={}, status="published")
        db.add_all([mock_exam, formal_exam])
        db.flush()
        now = datetime.now(timezone.utc).isoformat()
        db.add_all(
            [
                ExamResult(
                    exam_definition_id=mock_exam.id,
                    user_id=user.id,
                    score=100,
                    total_score=100,
                    passed=True,
                    published=True,
                    created_at=now,
                ),
                ExamResult(
                    exam_definition_id=formal_exam.id,
                    user_id=user.id,
                    score=50,
                    total_score=100,
                    passed=False,
                    published=False,  # 待复核/未公布
                    created_at=now,
                ),
            ]
        )
        db.commit()

        date_str = stats_service._date_str(datetime.now(timezone.utc))
        assert stats_service.refresh_daily(db, date_str) == 0
        assert db.query(StatsUserDaily).filter(StatsUserDaily.date == date_str).count() == 0
