"""认证服务流程测试：注册闭环、验证码、登录、改密。

重点回归：`send_code` 对已注册邮箱必须与未注册走**完全相同的成功响应**
（原实现抛 400，状态码本身就是注册邮箱枚举 oracle，与"统一提示"的注释意图相悖）。
"""

from __future__ import annotations

import secrets

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import delete, func, select

from app.core.errors import DomainError
from app.core.security import hash_password, verify_password
from app.database import db_session, init_db
from app.models.group import Group, UserGroup
from app.models.system import Setting
from app.models.user import EmailVerification, User
from app.services import auth_service, system_service


def _mk_user(email: str, *, verified: bool = True, status: str = "active", password: str | None = None) -> User:
    return User(
        email=email,
        password_hash=hash_password(password) if password else "x",
        name="用户",
        role="user",
        status=status,
        email_verified=verified,
    )


def _code_for(db, email: str) -> EmailVerification:
    row = (
        db.execute(
            select(EmailVerification).where(EmailVerification.email == email).order_by(EmailVerification.id.desc())
        )
        .scalars()
        .first()
    )
    assert row is not None
    return row


def _count_codes(db, email: str) -> int:
    return db.execute(
        select(func.count()).select_from(EmailVerification).where(EmailVerification.email == email)
    ).scalar_one()


@pytest.fixture(autouse=True)
def _smtp_configured(_fresh_db):
    """给本文件的用例提供占位 SMTP 配置。

    注册验证码链路在 SMTP 未配置时会 fail-closed 503（避免"提示已发送、用户永远收不到"），
    而本文件多数用例只关心验证码/账号状态；未配置时的 503 行为由
    `test_send_code_fails_closed_without_smtp` 单独覆盖。
    显式依赖 `_fresh_db` 以保证在本用例建表之后再写入配置。
    """
    with db_session() as db:
        db.add(Setting(setting_key="smtp_host", value="smtp.example.com", category="smtp", encrypted=False))
        db.commit()


# ---------- send_code ----------
def test_send_code_fails_closed_without_smtp():
    """SMTP 未配置时必须 503 且不写入验证码（原实现静默返回成功）。"""
    init_db()
    with db_session() as db:
        db.execute(delete(Setting).where(Setting.setting_key == "smtp_host"))
        db.commit()
        bg = BackgroundTasks()
        with pytest.raises((DomainError, HTTPException)) as exc:
            auth_service.send_code(db, "nosmtp@example.com", bg)
        assert exc.value.status_code == 503
        assert _count_codes(db, "nosmtp@example.com") == 0


def test_send_code_creates_verification_and_enqueues_mail():
    init_db()
    with db_session() as db:
        bg = BackgroundTasks()
        auth_service.send_code(db, "New@Example.com", bg)

        row = _code_for(db, "new@example.com")
        assert len(row.code) == 6
        assert row.used is False
        assert len(bg.tasks) == 1


def test_send_code_silently_ignores_existing_email():
    """防枚举回归：已注册邮箱不抛错、不发信、不新增验证码，响应与未注册一致。"""
    init_db()
    with db_session() as db:
        db.add(_mk_user("known@example.com"))
        db.commit()
        before = _count_codes(db, "known@example.com")

        bg = BackgroundTasks()
        auth_service.send_code(db, "known@example.com", bg)  # 不抛异常

        assert _count_codes(db, "known@example.com") == before
        assert bg.tasks == []


def test_send_code_respects_register_switch_and_suffix_whitelist():
    init_db()
    with db_session() as db:
        bg = BackgroundTasks()
        system_service.update_settings(db, "register", {"register_open": "false"})
        with pytest.raises((DomainError, HTTPException)) as closed:
            auth_service.send_code(db, "a@example.com", bg)
        assert closed.value.status_code == 403

        system_service.update_settings(db, "register", {"register_open": "true"})
        system_service.update_settings(db, "register", {"register_allowed_email_suffixes": "@corp.com,@edu.cn"})
        with pytest.raises((DomainError, HTTPException)) as suffix:
            auth_service.send_code(db, "a@gmail.com", bg)
        assert suffix.value.status_code == 400

        auth_service.send_code(db, "a@corp.com", bg)  # 白名单内放行
        assert _count_codes(db, "a@corp.com") == 1

        assert auth_service._allowed_suffixes(db) == ["@corp.com", "@edu.cn"]


# ---------- 注册 ----------
def test_register_consumes_code_and_activates_user():
    init_db()
    with db_session() as db:
        bg = BackgroundTasks()
        auth_service.send_code(db, "reg@example.com", bg)
        code = _code_for(db, "reg@example.com").code

        user = auth_service.register(db, "reg@example.com", "pw123456", "张三", code, bg)

        assert user.id
        assert user.status == "active"
        assert user.email_verified is True
        assert user.role == "user"
        assert verify_password("pw123456", user.password_hash)
        # 验证码单次消费
        assert _code_for(db, "reg@example.com").used is True


def test_register_requires_approval_when_configured():
    init_db()
    with db_session() as db:
        bg = BackgroundTasks()
        system_service.update_settings(db, "register", {"new_user_need_approve": "true"})
        auth_service.send_code(db, "pending@example.com", bg)
        code = _code_for(db, "pending@example.com").code

        user = auth_service.register(db, "pending@example.com", "pw123456", "", code, bg)
        assert user.status == "pending"


def test_register_group_rules():
    init_db()
    with db_session() as db:
        public = Group(name="公开组", type="自定义")
        private = Group(name="私有组", type="自定义")
        db.add_all([public, private])
        db.flush()
        db.commit()
        system_service.update_settings(
            db,
            "register",
            {"register_group_required": "true", "register_allowed_group_ids": str(public.id)},
        )
        bg = BackgroundTasks()

        # 缺分组 → 400
        auth_service.send_code(db, "g1@example.com", bg)
        code1 = _code_for(db, "g1@example.com").code
        with pytest.raises((DomainError, HTTPException)) as missing:
            auth_service.register(db, "g1@example.com", "pw123456", "", code1, bg, [])
        assert missing.value.status_code == 400

        # 非白名单分组 → 403
        auth_service.send_code(db, "g2@example.com", bg)
        code2 = _code_for(db, "g2@example.com").code
        with pytest.raises((DomainError, HTTPException)) as forbidden:
            auth_service.register(db, "g2@example.com", "pw123456", "", code2, bg, [private.id])
        assert forbidden.value.status_code == 403

        # 白名单分组 → 成功并建立关联
        auth_service.send_code(db, "g3@example.com", bg)
        code3 = _code_for(db, "g3@example.com").code
        user = auth_service.register(db, "g3@example.com", "pw123456", "", code3, bg, [public.id, public.id])
        links = db.execute(select(UserGroup.group_id).where(UserGroup.user_id == user.id)).scalars().all()
        assert links == [public.id]  # 去重


def test_register_rejects_bad_code_and_duplicate_email():
    init_db()
    with db_session() as db:
        bg = BackgroundTasks()
        auth_service.send_code(db, "dup@example.com", bg)
        with pytest.raises((DomainError, HTTPException)) as bad:
            auth_service.register(db, "dup@example.com", "pw123456", "", "000000", bg)
        assert bad.value.status_code == 400

        code = _code_for(db, "dup@example.com").code
        auth_service.register(db, "dup@example.com", "pw123456", "", code, bg)

        # 已注册邮箱即使拿到新验证码也不能重复注册
        db.add(User(email="x@example.com", password_hash="x", name="x", role="user", status="active"))
        db.commit()
        db.add(
            EmailVerification(
                email="dup@example.com",
                code="123456",
                expire_at="2999-01-01T00:00:00+00:00",
                used=False,
            )
        )
        db.commit()
        with pytest.raises((DomainError, HTTPException)) as dup:
            auth_service.register(db, "dup@example.com", "pw123456", "", "123456", bg)
        assert dup.value.status_code == 400
        assert "已注册" in dup.value.detail


# ---------- 旧版激活 / 重发 ----------
def test_verify_email_legacy_flow():
    init_db()
    with db_session() as db:
        db.add(_mk_user("old@example.com", verified=False, status="pending"))
        db.commit()
        db.add(
            EmailVerification(
                email="old@example.com",
                code="654321",
                expire_at="2999-01-01T00:00:00+00:00",
                used=False,
            )
        )
        db.commit()

        with pytest.raises((DomainError, HTTPException)):
            auth_service.verify_email(db, "old@example.com", "000000")

        auth_service.verify_email(db, "old@example.com", "654321")
        user = db.execute(select(User).where(User.email == "old@example.com")).scalar_one()
        assert user.email_verified is True
        assert user.status == "active"


def test_verify_email_rejects_unknown_and_expired():
    init_db()
    with db_session() as db:
        with pytest.raises((DomainError, HTTPException)) as unknown:
            auth_service.verify_email(db, "nobody@example.com", "123456")
        assert unknown.value.status_code == 400

        db.add(_mk_user("expired@example.com", verified=False))
        db.commit()
        db.add(
            EmailVerification(
                email="expired@example.com",
                code="111111",
                expire_at="2000-01-01T00:00:00+00:00",
                used=False,
            )
        )
        db.commit()
        with pytest.raises((DomainError, HTTPException)) as expired:
            auth_service.verify_email(db, "expired@example.com", "111111")
        assert expired.value.status_code == 400


def test_resend_only_sends_for_unverified_and_silent_for_unknown():
    init_db()
    with db_session() as db:
        bg = BackgroundTasks()
        auth_service.resend(db, "ghost@example.com", bg)  # 未知邮箱静默
        assert bg.tasks == []

        db.add(_mk_user("done@example.com", verified=True))
        db.add(_mk_user("waiting@example.com", verified=False))
        db.commit()

        auth_service.resend(db, "done@example.com", bg)
        assert bg.tasks == []

        auth_service.resend(db, "waiting@example.com", bg)
        assert len(bg.tasks) == 1
        assert _count_codes(db, "waiting@example.com") == 1


# ---------- 登录 / 改密 ----------
def test_login_success_and_failure_paths():
    init_db()
    with db_session() as db:
        db.add(_mk_user("ok@example.com", password="pw123456"))
        db.add(_mk_user("badpwd@example.com", password="pw123456"))
        db.add(_mk_user("unverified@example.com", verified=False, password="pw123456"))
        db.add(_mk_user("disabled@example.com", status="disabled", password="pw123456"))
        db.add(_mk_user("pending@example.com", status="pending", password="pw123456"))
        db.commit()

        token = auth_service.login(db, "OK@example.com", "pw123456")
        assert token["access_token"]
        assert token["token_type"] == "bearer"
        assert token["user"].email == "ok@example.com"

        with pytest.raises((DomainError, HTTPException)) as wrong:
            auth_service.login(db, "badpwd@example.com", "nope")
        assert wrong.value.status_code == 401

        with pytest.raises((DomainError, HTTPException)) as unknown:
            auth_service.login(db, "ghost@example.com", "pw123456")
        assert unknown.value.status_code == 401

        for email in ("unverified@example.com", "disabled@example.com", "pending@example.com"):
            with pytest.raises((DomainError, HTTPException)) as exc:
                auth_service.login(db, email, "pw123456")
            assert exc.value.status_code == 403


def test_change_password_bumps_token_version():
    init_db()
    with db_session() as db:
        user = _mk_user(f"{secrets.token_hex(3)}@example.com", password="oldpass1")
        db.add(user)
        db.commit()
        before = user.token_version

        with pytest.raises((DomainError, HTTPException)) as wrong:
            auth_service.change_password(db, user, "wrongpass", "newpass1")
        assert wrong.value.status_code == 400

        auth_service.change_password(db, user, "oldpass1", "newpass1")
        db.refresh(user)
        assert user.token_version == before + 1
        assert verify_password("newpass1", user.password_hash)
