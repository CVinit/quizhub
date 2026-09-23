"""一次性迁移：重建 stats_user_daily，移除已下线的考试类聚合列。

背景：排行榜下线（2026-09-23）后 `exam_count` / `exam_score_sum` / `exam_pass_count`
不再有任何读取方，「今日活跃」只按 `(user_id, date)` 计数，模型已移除这三列。
**存量库**的表里仍有这三列：不重建的话 `select(StatsUserDaily)` 会带上不存在的列名 →
`OperationalError`（每次概览刷新都会失败）。

做法（SQLite table-rebuild，比 `DROP COLUMN` 更兼容旧版本）：
1. `ALTER TABLE stats_user_daily RENAME TO stats_user_daily_old`；
2. 用**当前模型 DDL** 建新表（保证与 ORM 定义零漂移：列类型、唯一约束、外键）；
3. 拷贝共有的列；
4. 删除旧表（连带其索引），按模型重建显式索引。

幂等：旧表已无考试列时直接跳过。start.sh / 容器 entrypoint 会按文件名顺序自动执行。

用法：
    uv run python scripts/migrate_2026_09_23_stats_exam_columns.py            # 执行迁移（先备份）
    uv run python scripts/migrate_2026_09_23_stats_exam_columns.py --dry-run  # 仅报告将执行的变更
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

_TABLE = "stats_user_daily"
_OLD_TABLE = "stats_user_daily_old"
_DROPPED_COLUMNS = ("exam_count", "exam_score_sum", "exam_pass_count")


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}  # noqa: S608  表名为本模块常量


def needs_rebuild(conn: sqlite3.Connection) -> bool:
    """存量表是否仍带考试类聚合列（需要重建）。表不存在时返回 False。"""
    columns = _table_columns(conn, _TABLE)
    if not columns:
        return False
    return bool(columns & set(_DROPPED_COLUMNS))


def rebuild_table(conn: sqlite3.Connection) -> int:
    """按当前模型 DDL 重建表并保留数据，返回拷贝的行数。

    Args:
        conn: SQLite 连接（调用方负责 commit）。

    Returns:
        保留的行数。
    """
    from sqlalchemy.dialects import sqlite as sqlite_dialect
    from sqlalchemy.schema import CreateIndex, CreateTable

    from app.models.stats import StatsUserDaily

    table = StatsUserDaily.__table__
    dialect = sqlite_dialect.dialect()
    shared = [c.name for c in table.columns if c.name in _table_columns(conn, _TABLE)]

    conn.execute(f"ALTER TABLE {_TABLE} RENAME TO {_OLD_TABLE}")
    conn.execute(str(CreateTable(table).compile(dialect=dialect)))
    column_list = ", ".join(shared)
    conn.execute(f"INSERT INTO {_TABLE} ({column_list}) SELECT {column_list} FROM {_OLD_TABLE}")
    copied = int(conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0])
    conn.execute(f"DROP TABLE {_OLD_TABLE}")
    for index in table.indexes:
        conn.execute(str(CreateIndex(index).compile(dialect=dialect)))
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description="移除 stats_user_daily 的考试类聚合列")
    parser.add_argument("--dry-run", action="store_true", help="只报告将执行的变更，不写入数据库")
    args = parser.parse_args()

    if not DB_PATH.exists():
        logger.error("[migrate] 数据库不存在：%s，请先运行 init_db.py", DB_PATH)
        sys.exit(1)

    if not args.dry_run:
        # 备份 + 清理过旧备份：迁移每次启动都会跑，无上限的备份会撑爆磁盘
        backup_database(DB_PATH)

    logger.info("[migrate] 开始检查 stats_user_daily 的考试类聚合列：%s", DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        if not needs_rebuild(conn):
            logger.info("[migrate] 无需处理（表不存在或已无 %s）", "、".join(_DROPPED_COLUMNS))
            return
        if args.dry_run:
            logger.info("[migrate] dry-run：将重建 %s 并删除列 %s", _TABLE, "、".join(_DROPPED_COLUMNS))
            return
        copied = rebuild_table(conn)
        conn.commit()
        logger.info("[migrate] 完成：重建 %s（保留 %d 行），已删除 %s", _TABLE, copied, "、".join(_DROPPED_COLUMNS))
    except Exception as exc:  # noqa: BLE001  迁移脚本需给出明确失败信号
        logger.exception("[migrate] 迁移失败：%s", exc)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
