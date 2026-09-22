"""一次性迁移：补齐 stats_user_daily 的「未分组」唯一性与索引冗余。

仅适用于**已存在既有数据**的 SQLite 库；新库经 init_db 建表即带正确的索引，
无需运行本脚本。start.sh / 容器 entrypoint 会按文件名顺序自动执行本脚本。

背景：
1. `uq_user_daily(user_id, date, group_id)` 含可空列，而 NULL 在唯一约束中互不相等
   —— `group_id IS NULL` 的「未分组」行（只有 dept_group_id 或完全没有分组的用户）
   不受保护。历史并发刷新可能留下同 (user_id, date) 的多行，`rank()` 的
   `SUM(answer_count)` / `SUM(exam_score_sum)` 会重复计分。
2. `user_id` / `date` 上的单列索引分别被 `uq_user_daily`（user_id 前缀）与
   `ix_stats_date_user`（date 前缀）覆盖，属冗余索引，只增加写放大。

本脚本：
- 合并同 (user_id, date) 且 group_id IS NULL 的重复行：把其余行的计数/分数累加到
  id 最大的一行，再删除其余行（不丢数据）；
- 建部分唯一索引 `uq_user_daily_ungrouped`（幂等）；
- 删除冗余索引 `ix_stats_user_daily_user_id` / `ix_stats_user_daily_date`。

用法：
    uv run python scripts/migrate_2026_09_18.py            # 执行迁移（先备份）
    uv run python scripts/migrate_2026_09_18.py --dry-run  # 仅报告将执行的变更
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
_SUM_COLUMNS = (
    "answer_count",
    "correct_count",
    "wrong_count",
    "exam_count",
    "exam_score_sum",
    "exam_pass_count",
)
_PARTIAL_INDEX = "uq_user_daily_ungrouped"
_REDUNDANT_INDEXES = ("ix_stats_user_daily_user_id", "ix_stats_user_daily_date")


def _count_duplicate_groups(conn: sqlite3.Connection) -> int:
    """返回「未分组且同 (user_id, date) 有多行」的分组数。"""
    row = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT 1 FROM {_TABLE} WHERE group_id IS NULL "
        "GROUP BY user_id, date HAVING COUNT(*) > 1)"
    ).fetchone()
    return int(row[0]) if row else 0


def _count_removable_rows(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) FROM {_TABLE} WHERE group_id IS NULL AND id NOT IN "
        f"(SELECT MAX(id) FROM {_TABLE} WHERE group_id IS NULL GROUP BY user_id, date)"
    ).fetchone()
    return int(row[0]) if row else 0


def merge_ungrouped_duplicates(conn: sqlite3.Connection) -> int:
    """把未分组重复行合并到 id 最大的一行，返回删除的行数。"""
    keep_condition = f"id IN (SELECT MAX(id) FROM {_TABLE} WHERE group_id IS NULL GROUP BY user_id, date)"
    sums = ",\n            ".join(
        f"{column} = {column} + COALESCE((SELECT SUM(d.{column}) FROM {_TABLE} d "
        f"WHERE d.user_id = {_TABLE}.user_id AND d.date = {_TABLE}.date "
        f"AND d.group_id IS NULL AND d.id < {_TABLE}.id), 0)"
        for column in _SUM_COLUMNS
    )
    removed = _count_removable_rows(conn)
    # 先累加求和，再删重复行：顺序不能反，否则会丢计数
    conn.execute(f"UPDATE {_TABLE} SET\n            {sums}\n        WHERE group_id IS NULL AND {keep_condition}")
    conn.execute(
        f"DELETE FROM {_TABLE} WHERE group_id IS NULL AND id NOT IN "
        f"(SELECT MAX(id) FROM {_TABLE} WHERE group_id IS NULL GROUP BY user_id, date)"
    )
    return removed


def ensure_partial_unique_index(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_PARTIAL_INDEX} ON {_TABLE} (user_id, date) WHERE group_id IS NULL"
    )


def drop_redundant_indexes(conn: sqlite3.Connection) -> list[str]:
    dropped = []
    for name in _REDUNDANT_INDEXES:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
        if exists:
            conn.execute(f"DROP INDEX {name}")
            dropped.append(name)
    return dropped


def main() -> None:
    parser = argparse.ArgumentParser(description="补齐 stats_user_daily 唯一性并清理冗余索引")
    parser.add_argument("--dry-run", action="store_true", help="只报告将执行的变更，不写入数据库")
    args = parser.parse_args()

    if not DB_PATH.exists():
        logger.error("[migrate] 数据库不存在：%s，请先运行 init_db.py", DB_PATH)
        sys.exit(1)

    if not args.dry_run:
        # 备份 + 清理过旧备份：迁移每次启动都会跑，无上限的备份会撑爆磁盘
        backup_database(DB_PATH)

    logger.info("[migrate] 开始处理 stats_user_daily 唯一性与索引：%s", DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)).fetchone()
        if not exists:
            logger.info("[migrate] %s 表不存在，跳过", _TABLE)
            return

        groups = _count_duplicate_groups(conn)
        removable = _count_removable_rows(conn)
        if groups:
            logger.warning("[migrate] 发现 %d 组未分组重复行，将合并并删除 %d 行", groups, removable)
        else:
            logger.info("[migrate] 未发现未分组重复行")

        if args.dry_run:
            logger.info(
                "[migrate] dry-run：将合并 %d 组、删除 %d 行、创建部分唯一索引 %s、删除冗余索引 %s",
                groups,
                removable,
                _PARTIAL_INDEX,
                list(_REDUNDANT_INDEXES),
            )
            return

        removed = merge_ungrouped_duplicates(conn)
        ensure_partial_unique_index(conn)
        dropped = drop_redundant_indexes(conn)
        conn.commit()
        logger.info(
            "[migrate] 完成：合并删除 %d 行，创建索引 %s，删除冗余索引 %s",
            removed,
            _PARTIAL_INDEX,
            dropped or "无",
        )
    except Exception as exc:  # noqa: BLE001  迁移脚本需给出明确失败信号
        logger.exception("[migrate] 迁移失败：%s", exc)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
