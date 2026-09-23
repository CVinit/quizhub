"""一次性迁移：清理已下线排行榜的遗留设置行。

背景：排行榜（`GET /rank` + `services/stats/rank.py`）与 `rank_visible` 设置项已于
2026-09-23 按产品决策整体下线。设置项已从 `DEFAULT_SETTINGS` 移除，但**存量库**的
`settings` 表可能仍留着 `key='rank_visible'` 的行：`get_settings` 是「读全表」，
该孤儿行会让管理端设置页出现一个既无标签、也无法保存的字段。

本脚本删除该行（幂等）；新库经 init_db 建表本就不含该行。
start.sh / 容器 entrypoint 会按文件名顺序自动执行本脚本。

用法：
    uv run python scripts/migrate_2026_09_23.py            # 执行迁移（先备份）
    uv run python scripts/migrate_2026_09_23.py --dry-run  # 仅报告将执行的变更
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_PATH
from app.core.db_backup import backup_database

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("quizhub.migrate")

_SETTINGS_TABLE = "settings"
_LEGACY_KEYS = ("rank_visible",)


def count_legacy_rows(conn: sqlite3.Connection) -> int:
    """返回仍存在的遗留设置行数。

    Args:
        conn: SQLite 连接。

    Returns:
        命中 `_LEGACY_KEYS` 的行数（表不存在时返回 0）。
    """
    exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (_SETTINGS_TABLE,)).fetchone()
    if not exists:
        return 0
    placeholders = ",".join("?" for _ in _LEGACY_KEYS)
    row = conn.execute(
        f"SELECT COUNT(*) FROM {_SETTINGS_TABLE} WHERE key IN ({placeholders})",  # noqa: S608  表名/占位符均为本模块常量
        _LEGACY_KEYS,
    ).fetchone()
    return int(row[0]) if row else 0


def remove_legacy_rank_setting(conn: sqlite3.Connection) -> int:
    """删除遗留的排行榜设置行，返回删除行数（幂等）。

    Args:
        conn: SQLite 连接。

    Returns:
        实际删除的行数。
    """
    if count_legacy_rows(conn) == 0:
        return 0
    placeholders = ",".join("?" for _ in _LEGACY_KEYS)
    cursor = conn.execute(
        f"DELETE FROM {_SETTINGS_TABLE} WHERE key IN ({placeholders})",  # noqa: S608  同上
        _LEGACY_KEYS,
    )
    return int(cursor.rowcount or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="清理已下线排行榜的遗留设置行")
    parser.add_argument("--dry-run", action="store_true", help="只报告将执行的变更，不写入数据库")
    args = parser.parse_args()

    if not DB_PATH.exists():
        logger.error("[migrate] 数据库不存在：%s，请先运行 init_db.py", DB_PATH)
        sys.exit(1)

    if not args.dry_run:
        # 备份 + 清理过旧备份：迁移每次启动都会跑，无上限的备份会撑爆磁盘
        backup_database(DB_PATH)

    logger.info("[migrate] 开始清理排行榜遗留设置：%s", DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        pending = count_legacy_rows(conn)
        if not pending:
            logger.info("[migrate] 未发现遗留设置行（%s），无需处理", ", ".join(_LEGACY_KEYS))
            return
        if args.dry_run:
            logger.info("[migrate] dry-run：将删除 %d 行遗留设置（%s）", pending, ", ".join(_LEGACY_KEYS))
            return
        removed = remove_legacy_rank_setting(conn)
        conn.commit()
        logger.info("[migrate] 完成：已删除 %d 行遗留设置", removed)
    except Exception as exc:  # noqa: BLE001  迁移脚本需给出明确失败信号
        logger.exception("[migrate] 迁移失败：%s", exc)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
