"""补齐热点过滤列的索引（2026-09-19）。

背景：SQLite **不会**为外键自动建索引，且 `TimestampMixin.created_at` 也没有索引。
而以下列都是高频过滤条件：

- `users.dept_group_id`：部门数据范围过滤（`core.deps.user_ids_subquery`），
  几乎出现在每个部门管理员的列表/排行/审计/成绩查询里，且发生在分页之前；
- `users.role` / `users.status`：用户列表筛选、发布通知取活跃用户；
- `audit_logs.created_at` / `audit_logs.target_type`：审计表只增不减，按时间范围过滤 + count；
- `exam_results.created_at`：每日统计刷新（`stats/aggregate.refresh_daily`）；
- `short_answer_reviews.verdict`：复核队列按 `verdict IS NULL` / `= ?` 过滤；
- `questions.difficulty` / `question_banks.practice_enabled`：列表筛选与练习范围收敛；
- `exam_definitions.paper_template_id` / `created_by`：模板引用与 mock 定义复用/清理。

幂等：全部 `CREATE INDEX IF NOT EXISTS`；表不存在则跳过。索引名与模型声明
（`index=True` / `__table_args__`）生成的名称保持一致，确保「新建库（create_all）」与
「存量库（本迁移）」的 schema 收敛到同一状态。
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

# (索引名, 表名, 列名)
_INDEXES: list[tuple[str, str, str]] = [
    ("ix_users_role", "users", "role"),
    ("ix_users_status", "users", "status"),
    ("ix_users_dept_group_id", "users", "dept_group_id"),
    ("ix_audit_logs_created_at", "audit_logs", "created_at"),
    ("ix_audit_logs_target_type", "audit_logs", "target_type"),
    ("ix_exam_results_created_at", "exam_results", "created_at"),
    ("ix_short_answer_reviews_verdict", "short_answer_reviews", "verdict"),
    ("ix_questions_difficulty", "questions", "difficulty"),
    ("ix_question_banks_practice_enabled", "question_banks", "practice_enabled"),
    ("ix_exam_definitions_paper_template_id", "exam_definitions", "paper_template_id"),
    ("ix_exam_definitions_created_by", "exam_definitions", "created_by"),
]


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone() is not None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def create_missing_indexes(conn: sqlite3.Connection, dry_run: bool = False) -> list[str]:
    """补齐缺失的索引，返回本次（将）创建的索引名列表。

    Args:
        conn: 已连接的 SQLite 连接。
        dry_run: 为 True 时只统计不写入。

    Returns:
        需要创建（或已创建）的索引名列表。
    """
    created: list[str] = []
    for name, table, column in _INDEXES:
        if not _table_exists(conn, table):
            logger.info("[migrate] 表 %s 不存在，跳过索引 %s", table, name)
            continue
        if _index_exists(conn, name):
            continue
        if not dry_run:
            # 标识符全部来自模块常量，非用户输入
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")
        created.append(name)
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="补齐热点过滤列索引")
    parser.add_argument("--dry-run", action="store_true", help="只报告将执行的变更，不写入数据库")
    args = parser.parse_args()

    if not DB_PATH.exists():
        logger.error("[migrate] 数据库不存在：%s，请先运行 init_db.py", DB_PATH)
        sys.exit(1)

    if not args.dry_run:
        backup_database(DB_PATH)

    logger.info("[migrate] 开始补齐索引：%s", DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        created = create_missing_indexes(conn, args.dry_run)
        if not args.dry_run:
            conn.commit()
        logger.info(
            "[migrate] %s：新建索引 %d 个 %s",
            "dry-run" if args.dry_run else "完成",
            len(created),
            created or "无",
        )
    except Exception as exc:  # noqa: BLE001  迁移脚本需给出明确失败信号
        logger.exception("[migrate] 迁移失败：%s", exc)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
