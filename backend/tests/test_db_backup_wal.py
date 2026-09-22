"""`core.db_backup` 的 WAL 一致性回归测试。

背景：本库启用 `PRAGMA journal_mode=WAL`，已提交但未 checkpoint 的事务只存在于
`-wal` 边车文件中（进程被 kill 后是常态）。原实现只 `shutil.copy2` 主库文件，
备份会丢失最新已提交数据；且 `prune_backups` 会把这份坏备份当最新保留，
反而删掉更早的完整备份。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core import db_backup


def test_backup_includes_uncheckpointed_wal_data(tmp_path: Path):
    """WAL 中未 checkpoint 的已提交数据必须出现在备份里。"""
    db_path = tmp_path / "training.db"
    # 保持写连接打开，使已提交数据停留在 -wal 而不触发 checkpoint
    writer = sqlite3.connect(str(db_path))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    writer.execute("INSERT INTO t (v) VALUES ('wal-data')")
    writer.commit()
    assert (tmp_path / "training.db-wal").exists(), "前置条件：WAL 边车文件应存在"

    backup = db_backup.backup_database(db_path)
    assert backup is not None

    restored = sqlite3.connect(str(backup))
    assert restored.execute("SELECT v FROM t WHERE id=1").fetchone() == ("wal-data",)
    assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    restored.close()
    writer.close()


def test_backup_rejects_non_database_source(tmp_path: Path):
    """源文件不是合法 SQLite 库时降级返回 None，不向上抛异常、不留下坏备份。"""
    db_path = tmp_path / "training.db"
    db_path.write_bytes(b"not-a-database")

    assert db_backup.backup_database(db_path) is None
    assert not list(tmp_path.glob("training.db.bak-*")), "坏备份必须被丢弃"
