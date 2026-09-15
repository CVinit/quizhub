"""系统设置读取的健壮性回归测试。

背景（线上 500 事故）：
旧版 encrypt_value 在未配置 TRAINING_ENC_KEY 时回退写入 "plain:"+base64(value)，
却仍以 encrypted=1 入库（docs/audit_report.md SEC-P1-6）。该回退后来被移除，
decrypt_value 改为对 "plain:" 直接抛 RuntimeError，但**遗留数据没有迁移**，
导致 GET /system/settings?category=smtp 整体 500，管理端打不开邮件设置页。

两处修复：
1. get_settings 对无法解密的敏感项降级为空值并记录告警，单行损坏不再拖垮整个接口；
2. migrate_2026_08_28.migrate_legacy_plain_settings 清理遗留 plain: 行。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from app.core.security import encrypt_value
from app.database import db_session, init_db
from app.models.system import Setting
from app.services.system_service import get_settings


def _mk_setting(key: str, value: str, category: str, encrypted: bool) -> Setting:
    return Setting(setting_key=key, value=value, category=category, encrypted=encrypted)


def test_get_settings_survives_legacy_plain_value():
    """遗留 plain: 行不应让整个分类读取失败（回归：SMTP 设置 500）。"""
    init_db()
    with db_session() as db:
        db.add_all(
            [
                _mk_setting("smtp_host", "smtp.example.com", "smtp", False),
                # 模拟旧版回退写入的遗留值
                _mk_setting("smtp_password", "plain:", "smtp", True),
                _mk_setting("smtp_port", "465", "smtp", False),
            ]
        )
        db.commit()

        # 关键：不抛异常，且其它设置项正常返回
        settings = get_settings(db, "smtp")
        assert settings["smtp_host"] == "smtp.example.com"
        assert settings["smtp_port"] == "465"
        assert settings["smtp_password"] == "", "遗留不可解密项应降级为空值"


def test_get_settings_reads_valid_encrypted_value(monkeypatch):
    """正常的加密值仍能正确解密（降级逻辑不得误伤）。"""
    from cryptography.fernet import Fernet

    import app.core.security as security

    # 固定密钥（每次调用返回同一实例，保证加解密一致）
    f = Fernet(Fernet.generate_key())
    monkeypatch.setattr(security, "_fernet", lambda: f)

    init_db()
    with db_session() as db:
        db.add(_mk_setting("smtp_password", encrypt_value("s3cret"), "smtp", True))
        db.commit()
        assert get_settings(db, "smtp")["smtp_password"] == "s3cret"


def test_get_settings_all_categories_with_legacy_row():
    """无 category 参数（管理端一次性拉全部）同样不受遗留行影响。"""
    init_db()
    with db_session() as db:
        db.add_all(
            [
                _mk_setting("site_name", "平台", "general", False),
                _mk_setting("smtp_password", "plain:", "smtp", True),
            ]
        )
        db.commit()
        settings = get_settings(db, None)
        assert settings["site_name"] == "平台"
        assert settings["smtp_password"] == ""


def test_migration_clears_legacy_plain_rows(tmp_path: Path):
    """迁移脚本应清空遗留 plain: 行并置 encrypted=0。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.migrate_2026_08_28 import migrate_legacy_plain_settings

    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, category TEXT, encrypted INTEGER, id INTEGER)"
    )
    conn.execute("INSERT INTO settings (key, value, category, encrypted) VALUES ('smtp_password','plain:','smtp',1)")
    conn.execute("INSERT INTO settings (key, value, category, encrypted) VALUES ('site_name','平台','general',0)")
    conn.commit()

    migrate_legacy_plain_settings(conn)

    rows = {k: (v, e) for k, v, e in conn.execute("SELECT key, value, encrypted FROM settings").fetchall()}
    assert rows["smtp_password"] == ("", 0), "遗留 plain: 行未被清理"
    assert rows["site_name"] == ("平台", 0), "非遗留行不应被改动"
    conn.close()


def test_migration_is_idempotent(tmp_path: Path):
    """无遗留行时迁移应安全跳过（可重复执行）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.migrate_2026_08_28 import migrate_legacy_plain_settings

    db_file = tmp_path / "clean.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, category TEXT, encrypted INTEGER, id INTEGER)"
    )
    conn.execute("INSERT INTO settings (key, value, category, encrypted) VALUES ('site_name','平台','general',0)")
    conn.commit()

    migrate_legacy_plain_settings(conn)
    migrate_legacy_plain_settings(conn)
    assert conn.execute("SELECT value FROM settings WHERE key='site_name'").fetchone()[0] == "平台"
    conn.close()
