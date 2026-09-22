"""审查整改的安全与输入边界回归测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from fastapi import BackgroundTasks, HTTPException
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api import questions as questions_api
from app.core.errors import DomainError
from app.models.group import Group
from app.models.question import Question, QuestionBank
from app.models.record import ExamSession, QuestionState
from app.models.system import Setting
from app.models.user import User
from app.schemas.exam import ExamCreateIn
from app.schemas.question import QuestionCreate
from app.services import auth_service, mail_service, practice_service
from app.utils import excel


def _user(email: str, role: str = "user", group_id: int | None = None) -> User:
    return User(
        email=email,
        password_hash="x",
        name=email.split("@", 1)[0],
        role=role,
        status="active",
        email_verified=True,
        dept_group_id=group_id,
    )


def test_dept_admin_cannot_create_question_outside_scope():
    from app.core.deps import dept_scope_ids
    from app.database import db_session, init_db

    init_db()
    with db_session() as db:
        own = Group(name="本部门", type="部门")
        other = Group(name="其他部门", type="部门")
        db.add_all([own, other])
        db.flush()
        admin = _user("admin@quizhub.test", "dept_admin", own.id)
        db.add(admin)
        db.commit()

        payload = QuestionCreate(
            type="单选题",
            question="越权题目",
            options=["A", "B"],
            answer="A",
            group_id=other.id,
        )
        with pytest.raises((DomainError, HTTPException)) as exc:
            questions_api.create_question(payload, db, admin)
        assert exc.value.status_code == 403
        assert dept_scope_ids(db, admin) == {own.id}


def test_registration_is_rejected_when_registration_is_closed():
    from app.database import db_session, init_db
    from app.models.user import EmailVerification

    init_db()
    with db_session() as db:
        db.add(Setting(setting_key="register_open", value="false", category="register", encrypted=False))
        db.add(
            EmailVerification(
                email="new@quizhub.test",
                code="123456",
                expire_at="2999-01-01T00:00:00+00:00",
                used=False,
            )
        )
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            auth_service.register(
                db,
                "new@quizhub.test",
                "password123",
                "new",
                "123456",
                BackgroundTasks(),
            )
        assert exc.value.status_code == 403


def test_registration_cannot_self_assign_non_public_group():
    from app.database import db_session, init_db
    from app.models.user import EmailVerification

    init_db()
    with db_session() as db:
        group = Group(name="内部考试组", type="部门")
        db.add(group)
        db.flush()
        db.add(
            EmailVerification(
                email="new@quizhub.test",
                code="123456",
                expire_at="2999-01-01T00:00:00+00:00",
                used=False,
            )
        )
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            auth_service.register(
                db,
                "new@quizhub.test",
                "password123",
                "new",
                "123456",
                BackgroundTasks(),
                [group.id],
            )
        assert exc.value.status_code == 403


def test_short_eval_requires_an_existing_short_answer_record():
    from app.database import db_session, init_db

    init_db()
    with db_session() as db:
        user = _user("student@quizhub.test")
        db.add(user)
        db.flush()
        # 题库开放练习：本题只验证「非简答题不能自评」，
        # 需先通过练习题库校验，避免断言落到 403 而不是 400
        bank = QuestionBank(name="回归题库", practice_enabled=True)
        db.add(bank)
        db.flush()
        question = Question(
            bank_id=bank.id,
            type="单选题",
            question="客观题",
            options=["A", "B"],
            answer="A",
            analysis="",
            difficulty=1,
            tags=[],
            score=2,
        )
        db.add(question)
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            practice_service.short_eval(db, user.id, question.id, True)
        assert exc.value.status_code == 400
        assert db.execute(select(QuestionState).where(QuestionState.user_id == user.id)).first() is None


def test_mail_unconfigured_does_not_log_recipient_or_code(caplog):
    """SMTP 未配置的失败路径（经 send_safely 记录）不得泄露收件人或验证码。"""
    caplog.set_level("INFO", logger="quizhub")

    mail_service.send_safely(
        mail_service._send,
        "person@quizhub.test",
        "注册验证码",
        "验证码是 123456",
        {"smtp_host": ""},
    )

    assert "person@quizhub.test" not in caplog.text
    assert "123456" not in caplog.text
    assert "SMTP 未配置" in caplog.text


def test_encrypt_value_fails_closed_without_encryption_key(monkeypatch):
    monkeypatch.setattr("app.core.security.SETTINGS_ENC_KEY", "")

    with pytest.raises(RuntimeError):
        from app.core.security import encrypt_value

        encrypt_value("smtp-secret")


def test_masked_smtp_password_does_not_overwrite_secret():
    from app.database import db_session, init_db
    from app.services.system_service import update_settings

    init_db()
    with db_session() as db:
        db.add(Setting(setting_key="smtp_password", value="old-secret", category="smtp", encrypted=True))
        db.commit()

        update_settings(db, "smtp", {"smtp_password": "******"})

        assert db.get(Setting, 1).value == "old-secret"


def test_old_token_is_invalidated_after_password_change():
    from app.core.deps import get_current_user
    from app.core.security import create_access_token, hash_password
    from app.database import db_session, init_db

    init_db()
    with db_session() as db:
        user = _user("token@quizhub.test")
        user.password_hash = hash_password("old-password")
        db.add(user)
        db.commit()
        token = create_access_token(user.id, {"ver": user.token_version})
        user.token_version += 1
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            get_current_user(token, db)
        assert exc.value.status_code == 401


def test_verification_code_is_invalidated_after_failed_attempts():
    from app.database import db_session, init_db
    from app.models.user import EmailVerification

    init_db()
    with db_session() as db:
        db.add(
            EmailVerification(
                email="verify@quizhub.test",
                code="123456",
                expire_at="2999-01-01T00:00:00+00:00",
                used=False,
            )
        )
        db.commit()

        for _ in range(auth_service.MAX_VERIFY_ATTEMPTS):
            assert auth_service._consume_code(db, "verify@quizhub.test", "000000") is False

        verification = db.execute(select(EmailVerification)).scalar_one()
        assert verification.used is True
        assert verification.attempts == auth_service.MAX_VERIFY_ATTEMPTS


def test_parse_workbook_limits_rows(monkeypatch):
    monkeypatch.setattr(excel, "PARSE_ROW_MAX", 2, raising=False)
    wb = Workbook()
    ws = wb.active
    ws.title = "判断题"
    ws.append(["题干", "答案", "解析", "难度", "知识点标签", "分值", "所属分组ID"])
    ws.append(["题目1", "正确", "", 1, "", 1, ""])
    ws.append(["题目2", "正确", "", 1, "", 1, ""])
    ws.append(["题目3", "正确", "", 1, "", 1, ""])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    result = excel.parse_workbook(buf)

    assert result.total == 2


def test_exam_create_rejects_non_positive_duration():
    with pytest.raises(ValueError):
        ExamCreateIn(name="考试", duration_min=0)


def test_exam_duration_is_enforced_by_server():
    from app.database import db_session, init_db
    from app.models.exam import ExamDefinition
    from app.services import exam_service

    init_db()
    with db_session() as db:
        user = _user("student@quizhub.test")
        db.add(user)
        db.flush()
        question = Question(
            type="单选题",
            question="客观题",
            options=["A", "B"],
            answer="A",
            analysis="",
            difficulty=1,
            tags=[],
            score=10,
        )
        db.add(question)
        db.flush()
        exam = ExamDefinition(
            name="限时考试",
            type="formal",
            rules={},
            manual_questions=[question.id],
            group_ids=None,
            duration_min=1,
            pass_score=1,
            status="published",
        )
        db.add(exam)
        db.commit()

        session = exam_service.start_exam(db, user, exam.id)
        persisted = db.get(ExamSession, session["session_id"])
        assert persisted is not None
        persisted.started_at = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        db.commit()

        result = exam_service.submit_exam(db, user, persisted.id)

        assert result["overtime"] is True
        assert result["score"] == 0


def test_same_user_cannot_have_two_active_exam_sessions():
    from app.database import db_session, init_db
    from app.models.exam import ExamDefinition

    init_db()
    with db_session() as db:
        user = _user("student@quizhub.test")
        exam = ExamDefinition(name="考试", type="formal", rules={}, status="published")
        db.add_all([user, exam])
        db.flush()
        started = datetime.now(timezone.utc).isoformat()
        db.add(
            ExamSession(
                exam_definition_id=exam.id,
                user_id=user.id,
                status="in_progress",
                answers={},
                version=1,
                started_at=started,
            )
        )
        db.commit()
        db.add(
            ExamSession(
                exam_definition_id=exam.id,
                user_id=user.id,
                status="in_progress",
                answers={},
                version=1,
                started_at=started,
            )
        )

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            pytest.fail("同一用户不应存在两个进行中的考试会话")


def test_department_admin_cannot_publish_mixed_scope_exam():
    from app.database import db_session, init_db
    from app.models.exam import ExamDefinition
    from app.services import review_service

    init_db()
    with db_session() as db:
        own = Group(name="本部门", type="部门")
        other = Group(name="其他部门", type="部门")
        db.add_all([own, other])
        db.flush()
        exam = ExamDefinition(
            name="混合考试",
            type="formal",
            rules={},
            group_ids=[own.id, other.id],
            status="published",
        )
        db.add(exam)
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            review_service.publish_results(db, exam.id, {own.id})
        assert exc.value.status_code == 403


def test_department_admin_cannot_build_exam_from_out_of_scope_question():
    from app.database import db_session, init_db
    from app.services import exam_service

    init_db()
    with db_session() as db:
        own = Group(name="本部门", type="部门")
        other = Group(name="其他部门", type="部门")
        db.add_all([own, other])
        db.flush()
        admin = _user("admin@quizhub.test", "dept_admin", own.id)
        db.add(admin)
        db.flush()
        question = Question(
            type="单选题",
            question="其他部门题目",
            options=["A", "B"],
            answer="A",
            analysis="",
            difficulty=1,
            tags=[],
            score=2,
            group_id=other.id,
        )
        db.add(question)
        db.commit()

        payload = ExamCreateIn(
            name="越权考试",
            type="formal",
            manual_questions=[question.id],
            group_ids=[own.id],
        )
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.create_exam(db, payload, admin, {own.id})
        assert exc.value.status_code == 403
