"""SMTP 设置加密标记与注册分组的回归测试。

对应两个线上问题：
1. 填入 SMTP 配置后测试邮件/注册邮件都收不到 ——
   `smtp_password` 在库里是 `enc:` 密文，但 `encrypted` 标记位为 0，
   `get_settings` 只信标记位，把密文当明文密码去登录 SMTP（必然失败），
   且 SMTP 测试走后台任务，失败被吞掉、界面仍提示"已发送"。
2. 分组已创建，但注册页无法选择 ——
   「允许公开注册加入的分组」为空的 fail-closed 设计没有配置入口，
   管理端只能手填分组 ID，管理员无从得知。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

import app.core.security as security
from app.api import auth as auth_api
from app.api import system as system_api
from app.core.errors import DomainError
from app.models.group import Group
from app.models.system import Setting
from app.models.user import User
from app.schemas.system import SmtpTestIn
from app.services import mail_service
from app.services.system_service import get_settings, update_settings


@pytest.fixture
def fernet(monkeypatch):
    """固定一个 Fernet 实例，保证加解密在同一密钥下进行（测试库默认不配置 ENC_KEY）。"""
    f = Fernet(Fernet.generate_key())
    monkeypatch.setattr(security, "_fernet", lambda: f)
    return f


def _admin() -> User:
    return User(
        email="admin@quizhub.test",
        password_hash="x",
        name="管理员",
        role="super_admin",
        status="active",
        email_verified=True,
    )


def _no_rate_limit(monkeypatch):
    monkeypatch.setattr("app.core.rate_limit.check", lambda *a, **k: None)


# ---------- 问题 1：加密标记不一致 ----------


def test_get_settings_decrypts_enc_value_when_flag_is_false(fernet):
    """值是 enc: 密文但 encrypted=0 时，也必须解密，而不是把密文当明文返回。"""
    from app.database import db_session, init_db

    init_db()
    with db_session() as db:
        db.add(
            Setting(
                setting_key="smtp_password",
                value=security.encrypt_value("s3cret"),
                category="smtp",
                encrypted=False,  # 历史脏数据：标记与实际不符
            )
        )
        db.commit()
        assert get_settings(db, "smtp")["smtp_password"] == "s3cret"


def test_update_settings_repairs_encrypted_flag(fernet):
    """对已存在的历史行重写敏感值时，必须把 encrypted 标记一并回写。"""
    from app.database import db_session, init_db

    init_db()
    with db_session() as db:
        # 模拟 migrate_2026_08_28 清空遗留 plain: 行后的状态
        db.add(Setting(setting_key="smtp_password", value="", category="smtp", encrypted=False))
        db.commit()

        update_settings(db, "smtp", {"smtp_password": "new-secret"})

        row = db.query(Setting).filter(Setting.setting_key == "smtp_password").one()
        assert row.encrypted is True, "重写加密项后 encrypted 标记必须同步"
        assert row.value.startswith("enc:")
        assert get_settings(db, "smtp")["smtp_password"] == "new-secret"


# ---------- 问题 1：SMTP 测试必须回显真实失败原因 ----------


def test_smtp_test_returns_400_when_host_missing(monkeypatch):
    from app.database import db_session, init_db

    init_db()
    _no_rate_limit(monkeypatch)
    with db_session() as db, pytest.raises((DomainError, HTTPException)) as exc:
        system_api.smtp_test(SmtpTestIn(to_email="admin-recv@example.com"), db, _admin())
    assert exc.value.status_code == 400
    assert "SMTP" in str(exc.value.detail)


def test_smtp_test_surfaces_send_failure(monkeypatch):
    """SMTP 认证/连接失败必须返回 400 + 原始原因，而不是谎报"已发送"。"""
    from app.database import db_session, init_db

    init_db()
    _no_rate_limit(monkeypatch)

    def _boom(*_args, **_kwargs):
        raise mail_service.MailError("SMTPAuthenticationError: 535 authentication failed")

    monkeypatch.setattr(mail_service, "_send", _boom)
    with db_session() as db:
        db.add(Setting(setting_key="smtp_host", value="smtp.example.com", category="smtp", encrypted=False))
        db.commit()
        with pytest.raises((DomainError, HTTPException)) as exc:
            system_api.smtp_test(SmtpTestIn(to_email="admin-recv@example.com"), db, _admin())
    assert exc.value.status_code == 400
    assert "535" in str(exc.value.detail)


def test_smtp_test_reports_success_only_after_send(monkeypatch):
    from app.database import db_session, init_db

    init_db()
    _no_rate_limit(monkeypatch)
    sent: list[tuple] = []
    monkeypatch.setattr(mail_service, "_send", lambda *args: sent.append(args))
    with db_session() as db:
        db.add(Setting(setting_key="smtp_host", value="smtp.example.com", category="smtp", encrypted=False))
        db.commit()
        res = system_api.smtp_test(SmtpTestIn(to_email="admin-recv@example.com"), db, _admin())
    assert res["success"] is True
    assert len(sent) == 1, "测试接口应在返回成功前真正调用发送"


def test_mail_send_raises_when_sender_missing():
    with pytest.raises(mail_service.MailError):
        mail_service._send(
            "a@quizhub.test",
            "主题",
            "正文",
            {"smtp_host": "smtp.example.com", "smtp_sender": "", "smtp_username": ""},
        )


def test_mail_send_safely_swallows_and_logs(caplog):
    """后台任务包装：失败不能抛出（否则响应已发，异常无处理），但必须落日志。"""
    caplog.set_level("ERROR", logger="quizhub")

    def _boom(*_args, **_kwargs):
        raise mail_service.MailError("connection refused")

    mail_service.send_safely(_boom, "a@quizhub.test")

    assert "connection refused" in caplog.text


# ---------- 问题 2：注册分组（fail-closed 白名单 + 配置入口）----------


def test_register_groups_lists_only_whitelisted_groups():
    from app.database import db_session, init_db

    init_db()
    with db_session() as db:
        allowed = Group(name="允许注册", type="部门")
        denied = Group(name="不允许注册", type="部门")
        db.add_all([allowed, denied])
        db.flush()
        db.add(
            Setting(
                setting_key="register_allowed_group_ids",
                value=str(allowed.id),
                category="register",
                encrypted=False,
            )
        )
        db.commit()

        res = auth_api.register_groups(db)

    assert [g["id"] for g in res["groups"]] == [allowed.id]
    assert res["required"] is False


def test_register_groups_empty_without_whitelist():
    """未配置白名单时注册页无分组可选（保持 fail-closed），但不产生"必选却无选项"。"""
    from app.database import db_session, init_db

    init_db()
    with db_session() as db:
        db.add(Group(name="任意分组", type="部门"))
        db.add(
            Setting(
                setting_key="register_group_required",
                value="true",
                category="register",
                encrypted=False,
            )
        )
        db.commit()

        res = auth_api.register_groups(db)

    assert res["groups"] == []
    assert res["required"] is False, "无任何可选分组时不能要求必选，否则注册表单无法提交"


def test_register_groups_required_with_whitelist():
    from app.database import db_session, init_db

    init_db()
    with db_session() as db:
        group = Group(name="班级", type="班级")
        db.add(group)
        db.flush()
        db.add_all(
            [
                Setting(
                    setting_key="register_allowed_group_ids",
                    value=str(group.id),
                    category="register",
                    encrypted=False,
                ),
                Setting(
                    setting_key="register_group_required",
                    value="true",
                    category="register",
                    encrypted=False,
                ),
            ]
        )
        db.commit()

        res = auth_api.register_groups(db)

    assert [g["id"] for g in res["groups"]] == [group.id]
    assert res["required"] is True


# ---------- 数据修复迁移 ----------


def _mk_settings_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, category TEXT, encrypted INTEGER, id INTEGER)"
    )
    conn.executemany(
        "INSERT INTO settings (key, value, category, encrypted) VALUES (?,?,?,?)",
        [
            ("smtp_password", "enc:gAAAAA-cipher", "smtp", 0),
            ("smtp_port", "465", "smtp", 0),
            ("upload_allowed_ext", ".xlsx", "upload", 1),
            ("smtp_password_legacy", "plain:zzz", "smtp", 1),
        ],
    )
    conn.commit()
    return conn


def test_migration_repairs_encrypted_flags(tmp_path: Path):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.migrate_2026_09_17 import repair_encrypted_flags

    conn = _mk_settings_db(tmp_path / "flags.db")
    repair_encrypted_flags(conn, False)

    rows = {k: (v, e) for k, v, e in conn.execute("SELECT key, value, encrypted FROM settings").fetchall()}
    assert rows["smtp_password"] == ("enc:gAAAAA-cipher", 1), "密文行标记必须置 1"
    assert rows["smtp_port"] == ("465", 0), "普通行不受影响"
    assert rows["upload_allowed_ext"] == (".xlsx", 0), "标记为加密但值非密文时，标记归零、值保留"
    assert rows["smtp_password_legacy"] == ("", 0), "遗留 plain: 行应被清空"
    conn.close()


def test_migration_is_idempotent(tmp_path: Path):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.migrate_2026_09_17 import repair_encrypted_flags

    conn = _mk_settings_db(tmp_path / "flags2.db")
    repair_encrypted_flags(conn, False)
    before = conn.execute("SELECT key, value, encrypted FROM settings ORDER BY key").fetchall()
    repair_encrypted_flags(conn, False)
    after = conn.execute("SELECT key, value, encrypted FROM settings ORDER BY key").fetchall()
    assert before == after
    conn.close()
