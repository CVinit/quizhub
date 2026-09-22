"""迁移脚本健壮性回归测试。

背景（线上部署事故）：
启动脚本改为"每次启动按顺序执行全部迁移"后，migrate_2026_08_28.py 在**已迁移过**
的库上二次运行时报 `index ix_exam_questions_exam_definition_id already exists`。
原因是 exam_questions 重建流程：ALTER TABLE RENAME 会把原索引一并带走并保留索引名，
随后 CREATE INDEX 撞名失败 —— 而此时表已改名、数据尚未回填，库被留在
"exam_questions 为空、数据都在 exam_questions_old" 的危险中间态（实测 68 行数据
一度不可见）。

本测试固定三件事：
1. 重建流程在"已存在同名索引"的库上也能成功（索引名冲突不再中断）；
2. 中断状态可自动恢复，且数据不丢；
3. 迁移幂等，可重复执行。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migrate_2026_08_28.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("migrate_2026_08_28", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migrate_2026_08_28"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_old_schema_db(path: Path, rows: int = 3) -> None:
    """构造"未迁移"库：exam_questions 无唯一约束，但已有普通索引。"""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE exam_definitions (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE exam_questions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, exam_definition_id INTEGER NOT NULL,"
        "question_id INTEGER NOT NULL, seq INTEGER NOT NULL DEFAULT 0,"
        "score FLOAT NOT NULL DEFAULT 2, shuffle_map JSON)"
    )
    # 关键：先建好这两个索引，复现 RENAME 后的索引名冲突
    conn.execute("CREATE INDEX ix_exam_questions_exam_definition_id ON exam_questions (exam_definition_id)")
    conn.execute("CREATE INDEX ix_exam_questions_question_id ON exam_questions (question_id)")
    conn.execute("INSERT INTO exam_definitions (id) VALUES (1)")
    for i in range(1, rows + 1):
        conn.execute("INSERT INTO questions (id) VALUES (?)", (i,))
        conn.execute(
            "INSERT INTO exam_questions (exam_definition_id, question_id, seq, score) VALUES (1, ?, ?, 2)",
            (i, i - 1),
        )
    conn.commit()
    conn.close()


def _index_names(conn: sqlite3.Connection, table: str = "exam_questions") -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,))}


def test_rebuild_handles_preexisting_index_names(tmp_path: Path):
    """已存在同名索引时重建仍应成功（回归：index already exists 中断迁移）。"""
    m = _load_module()
    db = tmp_path / "old.db"
    _make_old_schema_db(db, rows=3)

    conn = sqlite3.connect(str(db))
    m.migrate_exam_question_unique(conn)

    assert conn.execute("SELECT COUNT(*) FROM exam_questions").fetchone()[0] == 3, "数据丢失"
    assert "uq_exam_question" in _index_names(conn)
    assert "ix_exam_questions_exam_definition_id" in _index_names(conn)
    assert "ix_exam_questions_question_id" in _index_names(conn)
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name='exam_questions_old'").fetchall()
    conn.close()


def test_migration_is_idempotent(tmp_path: Path):
    """重复执行不应报错、不应改变数据。"""
    m = _load_module()
    db = tmp_path / "twice.db"
    _make_old_schema_db(db, rows=4)

    conn = sqlite3.connect(str(db))
    m.migrate_exam_question_unique(conn)
    m.migrate_exam_question_unique(conn)
    m.migrate_exam_question_unique(conn)
    assert conn.execute("SELECT COUNT(*) FROM exam_questions").fetchone()[0] == 4
    conn.close()


def test_recovers_from_interrupted_rebuild(tmp_path: Path):
    """中断态（新表空、数据在 _old）应自动恢复且不丢数据。"""
    m = _load_module()
    db = tmp_path / "broken.db"
    _make_old_schema_db(db, rows=5)

    # 制造中断现场：RENAME + 建空表（数据尚未回填）
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("ALTER TABLE exam_questions RENAME TO exam_questions_old")
    conn.execute(
        "CREATE TABLE exam_questions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, exam_definition_id INTEGER NOT NULL,"
        "question_id INTEGER NOT NULL, seq INTEGER NOT NULL DEFAULT 0,"
        "score FLOAT NOT NULL DEFAULT 2, shuffle_map JSON)"
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM exam_questions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM exam_questions_old").fetchone()[0] == 5

    # 再跑迁移应自动恢复
    m.migrate_exam_question_unique(conn)

    assert conn.execute("SELECT COUNT(*) FROM exam_questions").fetchone()[0] == 5, "中断态未被恢复"
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name='exam_questions_old'").fetchall()
    assert "uq_exam_question" in _index_names(conn)
    conn.close()


def test_duplicate_rows_abort_migration(tmp_path: Path):
    """存在重复行时应拒绝迁移（exit 1），避免唯一约束建立失败留下半成品。"""
    m = _load_module()
    db = tmp_path / "dups.db"
    _make_old_schema_db(db, rows=2)

    conn = sqlite3.connect(str(db))
    # 造重复：(exam 1, question 1) 出现两次
    conn.execute("INSERT INTO exam_questions (exam_definition_id, question_id, seq, score) VALUES (1, 1, 9, 2)")
    conn.commit()

    with pytest.raises(SystemExit) as exc:
        m.migrate_exam_question_unique(conn)
    assert exc.value.code == 1
    # 未破坏原表
    assert conn.execute("SELECT COUNT(*) FROM exam_questions").fetchone()[0] == 3
    conn.close()


def test_legacy_plain_settings_still_handled(tmp_path: Path):
    """同文件内的遗留设置清理逻辑仍可用。"""
    m = _load_module()
    db = tmp_path / "plain.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, category TEXT, encrypted INTEGER, id INTEGER)"
    )
    conn.execute("INSERT INTO settings (key,value,category,encrypted) VALUES ('smtp_password','plain:','smtp',1)")
    conn.commit()

    m.migrate_legacy_plain_settings(conn)

    val, enc = conn.execute("SELECT value, encrypted FROM settings WHERE key='smtp_password'").fetchone()
    assert (val, enc) == ("", 0)
    conn.close()


# ---------- migrate_2026_09_16: users.email NOCASE 重建 ----------
_SCRIPT_0916 = Path(__file__).resolve().parent.parent / "scripts" / "migrate_2026_09_16.py"


def _load_module_0916():
    spec = importlib.util.spec_from_file_location("migrate_2026_09_16", _SCRIPT_0916)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migrate_2026_09_16"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_users_db(path: Path) -> None:
    """构造未迁移库：users.email 为 BINARY，且带有重建前必须保留的索引。"""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE groups (id INTEGER PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE users ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,"
        "email VARCHAR NOT NULL,"
        "password_hash VARCHAR NOT NULL,"
        "name VARCHAR NOT NULL,"
        "role VARCHAR NOT NULL,"
        "status VARCHAR NOT NULL,"
        "email_verified BOOLEAN NOT NULL,"
        "token_version INTEGER NOT NULL,"
        "dept_group_id INTEGER REFERENCES groups (id) ON DELETE SET NULL,"
        "created_at VARCHAR NOT NULL,"
        "UNIQUE (email))"
    )
    conn.execute("CREATE UNIQUE INDEX ix_users_email ON users (email)")
    conn.execute("CREATE INDEX ix_users_role ON users (role)")
    for email, name in (("Admin@X.com", "a"), ("a@x.com", "b"), ("B@x.com", "c")):
        conn.execute(
            "INSERT INTO users (email,password_hash,name,role,status,email_verified,token_version,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (email, "h", name, "user", "active", 1, 0, "2026-01-01T00:00:00+00:00"),
        )
    conn.commit()
    conn.close()


def test_email_collation_rebuild_keeps_indexes_consistent(tmp_path: Path):
    """回归：改 email collation 必须重建索引，否则 integrity_check 报索引损坏。

    原实现用 `PRAGMA writable_schema` 只改表定义，既有索引仍按 BINARY 排序，
    `PRAGMA integrity_check` 会报 "row N missing from index" / "non-unique entry"。
    """
    m = _load_module_0916()
    db = tmp_path / "collation.db"
    _make_users_db(db)

    conn = sqlite3.connect(str(db))
    m.migrate_email_collation(conn, dry_run=False)
    conn.commit()

    assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "重建后索引必须一致"
    # NOCASE 生效：小写查询能命中混合大小写邮箱
    assert conn.execute("SELECT COUNT(*) FROM users WHERE email='admin@x.com'").fetchone()[0] == 1
    # 大小写变体不得再重复注册
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO users (email,password_hash,name,role,status,email_verified,token_version,created_at) "
            "VALUES ('ADMIN@x.com','h','d','user','active',1,0,'2026-01-01T00:00:00+00:00')"
        )
    # 既有索引随表重建保留
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='users'")}
    assert {"ix_users_email", "ix_users_role"} <= names, f"索引丢失：{names}"
    conn.close()


def test_email_collation_is_idempotent(tmp_path: Path):
    """已为 NOCASE 的库重复执行不得报错、不得丢数据。"""
    m = _load_module_0916()
    db = tmp_path / "collation_twice.db"
    _make_users_db(db)

    conn = sqlite3.connect(str(db))
    m.migrate_email_collation(conn, dry_run=False)
    m.migrate_email_collation(conn, dry_run=False)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3
    assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    conn.close()
