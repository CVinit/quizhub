"""本轮评审 4 项高优先 suggestions（加固类）的回归测试。

1. S1 迁移备份有界：`backup_database` 必须清理过旧的自动备份，且**不得**动人工命名的备份；
   同时 `migrate_2026_08_28.py` 必须拒绝未知参数（回归：`--dry-run` 被静默忽略后真的迁移）。
2. S2 畸形 `TRAINING_ENC_KEY`（非空但非法）不得让设置读取接口 500，应降级为空值。
3. S3 `save_draft` 必须是原子 upsert（回归：check-then-insert 并发撞唯一约束）。
4. S4 注册邮箱后缀白名单必须强制 `@` 前缀（否则 `evil-company.com` 命中 `company.com`）。
"""

from __future__ import annotations

import importlib.util
import re
import secrets
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.errors import DomainError
from app.core.security import hash_password
from app.database import SessionLocal, db_session, init_db
from app.models.system import Draft, Setting
from app.models.user import User
from app.services import audit_service, auth_service, system_service


def _mk_user(db) -> User:
    user = User(
        email=f"{secrets.token_hex(4)}@example.com",
        password_hash=hash_password("pw123456"),
        name="用户",
        role="user",
        status="active",
        email_verified=True,
    )
    db.add(user)
    db.flush()
    return user


# ---------- S2：畸形加密密钥 ----------
def test_malformed_enc_key_is_a_value_error():
    """记录机制：非法 Fernet 密钥抛 ValueError，且它不是 RuntimeError/InvalidToken。"""
    with pytest.raises(ValueError):
        Fernet(b"not-a-valid-fernet-key")


def test_settings_read_survives_malformed_enc_key(monkeypatch):
    """密钥非空但格式非法时，读取设置应降级为空值而不是整体 500。"""
    init_db()
    with db_session() as db:
        db.add(Setting(setting_key="smtp_password", value="enc:whatever", category="smtp", encrypted=True))
        db.commit()

    # 模拟被误填/截断的 TRAINING_ENC_KEY
    monkeypatch.setattr("app.core.security.SETTINGS_ENC_KEY", "not-a-valid-fernet-key")

    with db_session() as db:
        out = system_service.get_settings(db, "smtp")

    assert out["smtp_password"] == "", "坏密钥必须降级为空值，而不是让设置页 500"


# ---------- S4：注册邮箱后缀白名单 ----------
def test_email_suffix_setting_rejects_missing_at_sign():
    """缺 "@" 的后缀会让 evil-company.com 命中 company.com，必须拒绝保存。"""
    init_db()
    with db_session() as db:
        with pytest.raises(ValueError):
            system_service.update_settings(db, "register", {"register_allowed_email_suffixes": "company.com"})
        with pytest.raises(ValueError):
            system_service.update_settings(db, "register", {"register_allowed_email_suffixes": "m"})
        with pytest.raises(ValueError):
            system_service.update_settings(db, "register", {"register_allowed_email_suffixes": "@nodot"})


def test_email_suffix_whitelist_is_enforced_and_parses_chinese_comma():
    init_db()
    with db_session() as db:
        # 中文逗号分隔必须与英文逗号等价（与 register_allowed_group_ids 口径一致）
        system_service.update_settings(db, "register", {"register_allowed_email_suffixes": "@corp.com，@edu.cn"})
        assert auth_service._allowed_suffixes(db) == ["@corp.com", "@edu.cn"]

        auth_service.check_email_suffix(db, "alice@corp.com")
        auth_service.check_email_suffix(db, "bob@edu.cn")

        with pytest.raises((DomainError, HTTPException)) as exc:
            auth_service.check_email_suffix(db, "mallory@evil-corp.com")
        assert exc.value.status_code == 400, "近似域名（evil-corp.com）不得命中 @corp.com"

        with pytest.raises((DomainError, HTTPException)):
            auth_service.check_email_suffix(db, "mallory@other.com")


# ---------- S3：草稿 upsert ----------
def test_save_draft_upserts_single_row():
    init_db()
    with db_session() as db:
        user = _mk_user(db)
        db.commit()
        uid = user.id

    with db_session() as db:
        audit_service.save_draft(db, uid, "exam-form", {"a": 1})
    with db_session() as db:
        audit_service.save_draft(db, uid, "exam-form", {"a": 2})
        row = db.execute(select(Draft).where(Draft.user_id == uid, Draft.form_key == "exam-form")).scalar_one()
        count = db.execute(select(func.count()).select_from(Draft).where(Draft.user_id == uid)).scalar_one()

    assert row.payload == {"a": 2}
    assert count == 1


def test_concurrent_save_draft_never_raises(monkeypatch):
    """并发自动保存不得因唯一约束冲突报错（回归：check-then-insert 竞态 → 500）。

    单纯并发调用很难稳定命中：SQLite 写锁会把各线程串行化到「读-判」窗口之外。
    这里把 `_now()` 放慢，**确定性地**撑开「判定不存在」与「写入」之间的窗口 ——
    旧实现（先 SELECT 再 INSERT）会让多个线程都读到"不存在"，从而撞唯一约束；
    upsert 是单条原子语句，不存在该窗口，因此必须全部成功且只留一行。
    """
    init_db()
    with db_session() as db:
        user = _mk_user(db)
        db.commit()
        uid = user.id

    real_now = audit_service._now

    def slow_now() -> str:
        time.sleep(0.05)
        return real_now()

    monkeypatch.setattr(audit_service, "_now", slow_now)

    workers = 6
    barrier = threading.Barrier(workers)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            audit_service.save_draft(db, uid, "form", {"n": n})
        except BaseException as exc:  # noqa: BLE001  测试需要收集所有失败类型
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"并发保存出现异常：{errors[:3]}"
    with db_session() as db:
        count = db.execute(select(func.count()).select_from(Draft).where(Draft.user_id == uid)).scalar_one()
    assert count == 1, "同一 (user, form_key) 只应有一行"


# ---------- S1：备份有界 + 08_28 拒绝未知参数 ----------
_AUTO_BACKUP_RE = re.compile(r"\.db\.bak-\d{14}$")


def test_backup_database_prunes_old_auto_backups(tmp_path: Path):
    """自动备份必须收敛到上限，且人工命名的备份一律保留。"""
    from app.core.db_backup import backup_database

    db_path = tmp_path / "training.db"
    # 必须是真实的 SQLite 库：备份走 SQLite 在线备份 API 并校验完整性，
    # 伪造的字节串会被判定为「不是数据库」而拒绝备份。
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('x')")
    conn.commit()
    conn.close()

    base = datetime(2020, 1, 1)
    for i in range(8):
        stale = tmp_path / f"training.db.bak-{(base + timedelta(days=i)).strftime('%Y%m%d%H%M%S')}"
        stale.write_bytes(b"old")

    manual = tmp_path / "training.db.bak-before-repair"
    manual.write_bytes(b"manual")

    created = backup_database(db_path, keep=3)

    autos = sorted(p.name for p in tmp_path.glob("training.db.bak-*") if _AUTO_BACKUP_RE.search(p.name))
    assert len(autos) == 3, f"自动备份必须收敛到 3 份，实际 {autos}"
    assert created is not None and created.exists(), "最新备份不得被自己清理掉"
    assert manual.exists(), "人工命名的备份（bak-before-repair）不得被清理"
    # 被删的应是最旧的那几份
    assert autos[-1] == created.name


def test_migration_08_28_rejects_unknown_flags(monkeypatch):
    """回归：08_28 原实现不解析参数，--dry-run 被静默忽略后**真的执行迁移**。"""
    script = Path(__file__).resolve().parent.parent / "scripts" / "migrate_2026_08_28.py"
    spec = importlib.util.spec_from_file_location("migrate_2026_08_28_argcheck", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_2026_08_28_argcheck"] = module
    spec.loader.exec_module(module)

    monkeypatch.setattr(sys, "argv", ["migrate_2026_08_28.py", "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        module.main()

    assert exc.value.code == 2, "未知参数必须以 argparse 错误退出，而不是继续执行迁移"
