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

**事务性（重要）**：整段重建包在显式 `BEGIN … COMMIT` 里。Python 的 sqlite3 不会为 DDL
自动开启事务（实测 `ALTER TABLE … RENAME` 后 `in_transaction` 仍为 False，改动立即对其它
连接可见），因此原实现一旦在「已 RENAME、未回填」之间中断，`stats_user_daily` 会缺失而数据
全部滞留在 `_old` 表；重跑又因 `needs_rebuild` 对不存在的表返回 False 而打印「无需处理」，
历史聚合静默丢失。现在中断会整体回滚，并且额外提供 `recover_interrupted_rebuild()` 兜底
（与 scripts/migrate_2026_08_28.py 的同名机制同口径）。

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


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608  表名为本模块常量


def _begin(conn: sqlite3.Connection) -> None:
    """开启显式事务（DDL 不会被 sqlite3 自动纳入事务）。"""
    if not conn.in_transaction:
        conn.execute("BEGIN")


def needs_rebuild(conn: sqlite3.Connection) -> bool:
    """存量表是否仍带考试类聚合列（需要重建）。表不存在时返回 False。"""
    columns = _table_columns(conn, _TABLE)
    if not columns:
        return False
    return bool(columns & set(_DROPPED_COLUMNS))


def needs_recovery(conn: sqlite3.Connection) -> bool:
    """是否存在「已 RENAME 但未回填」的中断残留。

    特征：`stats_user_daily_old` 有数据，而目标表缺失、或行数比 old 表少。
    正常完成的重建会 DROP 掉 old 表，因此不会误判。
    """
    if not _table_exists(conn, _OLD_TABLE):
        return False
    old_count = _row_count(conn, _OLD_TABLE)
    if old_count == 0:
        return False
    if not _table_exists(conn, _TABLE):
        return True
    return _row_count(conn, _TABLE) < old_count


def _create_table_and_copy(conn: sqlite3.Connection, source_table: str) -> int:
    """按当前模型 DDL 建表，并从 `source_table` 拷贝共有列，返回拷贝行数。

    调用方需已开启事务，并在删除 old 表之后调用 `_create_indexes()`。

    Args:
        conn: SQLite 连接。
        source_table: 数据来源表（`_OLD_TABLE`）。

    Returns:
        拷贝到新表的行数。
    """
    from sqlalchemy.dialects import sqlite as sqlite_dialect
    from sqlalchemy.schema import CreateTable

    from app.models.stats import StatsUserDaily

    table = StatsUserDaily.__table__
    dialect = sqlite_dialect.dialect()
    # 只拷贝两边都存在的列：old 表可能仍带已删除的考试类聚合列
    source_columns = _table_columns(conn, source_table)
    shared = [c.name for c in table.columns if c.name in source_columns]

    conn.execute(str(CreateTable(table).compile(dialect=dialect)))
    column_list = ", ".join(shared)
    conn.execute(f"INSERT INTO {_TABLE} ({column_list}) SELECT {column_list} FROM {source_table}")
    return _row_count(conn, _TABLE)


def _create_indexes(conn: sqlite3.Connection) -> None:
    """按模型重建该表的显式索引。

    **必须在 DROP 掉 old 表之后调用**：SQLite 的 `ALTER TABLE … RENAME` 会把原索引一并
    带走且**保留索引名**，old 表还在时就建同名索引会报
    `index ix_stats_date_user already exists`（实跑复现：对存量库执行时中断）。

    调用方需已开启事务。

    Args:
        conn: SQLite 连接。
    """
    from sqlalchemy.dialects import sqlite as sqlite_dialect
    from sqlalchemy.schema import CreateIndex

    from app.models.stats import StatsUserDaily

    table = StatsUserDaily.__table__
    dialect = sqlite_dialect.dialect()
    for index in table.indexes:
        conn.execute(str(CreateIndex(index).compile(dialect=dialect)))


def recover_interrupted_rebuild(conn: sqlite3.Connection) -> int:
    """从「已 RENAME 但未回填」的中断状态恢复数据。

    策略：以 old 表为准重建目标表并回填，最后删除 old 表。整体在一个事务内，失败即回滚，
    不会把「部分恢复」留在库里。

    Args:
        conn: SQLite 连接。

    Returns:
        恢复的行数。

    Raises:
        Exception: 恢复过程中的任何错误（回滚后向上抛，由调用方决定退出码）。
    """
    _begin(conn)
    try:
        if _table_exists(conn, _TABLE):
            # 目标表存在但行数不足（中断在 CREATE 与 INSERT 之间）：整表重建
            conn.execute(f"DROP TABLE {_TABLE}")
        copied = _create_table_and_copy(conn, _OLD_TABLE)
        conn.execute(f"DROP TABLE {_OLD_TABLE}")
        _create_indexes(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return copied


def rebuild_table(conn: sqlite3.Connection) -> int:
    """按当前模型 DDL 重建表并保留数据，返回拷贝的行数。

    整段重建在显式事务内完成：sqlite3 不会为 DDL 自动开启事务，不显式包起来的话
    中断会留下「表缺失 + 数据滞留 old 表」的半损状态。

    Args:
        conn: SQLite 连接。

    Returns:
        保留的行数。

    Raises:
        Exception: 重建过程中的任何错误（回滚后向上抛）。
    """
    _begin(conn)
    try:
        conn.execute(f"ALTER TABLE {_TABLE} RENAME TO {_OLD_TABLE}")
        copied = _create_table_and_copy(conn, _OLD_TABLE)
        conn.execute(f"DROP TABLE {_OLD_TABLE}")
        # 索引必须在 old 表删除后建：RENAME 会把原索引名带到 old 表上（见 _create_indexes）
        _create_indexes(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
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
        interrupted = needs_recovery(conn)
        if args.dry_run:
            if interrupted:
                logger.info("[migrate] dry-run：检测到上次迁移中断残留（%s），将先恢复再重建", _OLD_TABLE)
            if needs_rebuild(conn):
                logger.info("[migrate] dry-run：将重建 %s 并删除列 %s", _TABLE, "、".join(_DROPPED_COLUMNS))
            else:
                logger.info("[migrate] dry-run：无需处理")
            return
        if interrupted:
            # 先救回数据，再按需重建：不这样做的话下一步会因目标表缺失而误判「无需处理」
            recovered = recover_interrupted_rebuild(conn)
            logger.warning("[migrate] 检测到上次迁移中断，已从 %s 恢复 %d 行", _OLD_TABLE, recovered)
        if not needs_rebuild(conn):
            logger.info("[migrate] 无需处理（表不存在或已无 %s）", "、".join(_DROPPED_COLUMNS))
            return
        copied = rebuild_table(conn)
        logger.info("[migrate] 完成：重建 %s（保留 %d 行），已删除 %s", _TABLE, copied, "、".join(_DROPPED_COLUMNS))
    except Exception as exc:  # noqa: BLE001  迁移脚本需给出明确失败信号
        logger.exception("[migrate] 迁移失败：%s", exc)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
