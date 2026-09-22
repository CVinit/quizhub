"""一次性迁移：题库练习开关 + 考试归档。

变更内容（与 models 当前定义对齐）：
1. question_banks 增加 practice_enabled BOOLEAN NOT NULL DEFAULT 1 ——
   控制该题库是否对用户开放练习。既有题库默认开启，行为不变。
2. exam_definitions.status 支持 'archived'（归档）—— 无需改表结构（status 为 TEXT），
   本脚本仅把历史遗留的测试考试做一次性归档收尾，由 --archive-exam 参数显式指定。

用法：uv run python scripts/migrate_2026_09_14.py
      uv run python scripts/migrate_2026_09_14.py --archive-exam 4 --archive-exam 5

仅适用于已存在既有数据的 SQLite 库；新库经 init_db 建表已含这些定义，无需运行。
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


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in cols


def migrate_bank_practice_enabled(conn: sqlite3.Connection) -> None:
    """question_banks.practice_enabled：默认 1（开启），保证既有题库行为不变。"""
    if _table_has_column(conn, "question_banks", "practice_enabled"):
        logger.info("[migrate] question_banks.practice_enabled 已存在，跳过")
        return
    logger.info("[migrate] 为 question_banks 增加 practice_enabled 列 ...")
    conn.execute("ALTER TABLE question_banks ADD COLUMN practice_enabled BOOLEAN NOT NULL DEFAULT 1")
    conn.commit()
    logger.info("[migrate] question_banks.practice_enabled 已添加（既有题库默认开启练习）")


def archive_exams(conn: sqlite3.Connection, exam_ids: list[int]) -> None:
    """将指定考试置为归档状态（对用户隐藏，保留成绩可追溯）。"""
    for exam_id in exam_ids:
        row = conn.execute("SELECT name, status FROM exam_definitions WHERE id=?", (exam_id,)).fetchone()
        if not row:
            logger.warning("[migrate] 考试 id=%s 不存在，跳过", exam_id)
            continue
        name, status = row
        if status == "archived":
            logger.info("[migrate] 考试 id=%s「%s」已归档，跳过", exam_id, name)
            continue
        conn.execute("UPDATE exam_definitions SET status='archived' WHERE id=?", (exam_id,))
        conn.commit()
        logger.info("[migrate] 考试 id=%s「%s」已归档（原状态 %s）", exam_id, name, status)


def main() -> None:
    parser = argparse.ArgumentParser(description="题库练习开关 + 考试归档迁移")
    parser.add_argument(
        "--archive-exam",
        type=int,
        action="append",
        default=[],
        metavar="ID",
        help="把指定考试置为归档状态（可重复指定）",
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        logger.error("[migrate] 数据库不存在：%s，请先运行 init_db.py", DB_PATH)
        sys.exit(1)
    # 与其它迁移保持一致：先备份再写，且备份数量有上限（见 app/core/db_backup.py）
    backup_database(DB_PATH)
    logger.info("[migrate] 开始迁移：%s", DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        migrate_bank_practice_enabled(conn)
        if args.archive_exam:
            archive_exams(conn, args.archive_exam)
    except Exception as exc:  # noqa: BLE001  迁移脚本需给出明确失败信号
        logger.exception("[migrate] 迁移失败：%s", exc)
        sys.exit(1)
    finally:
        conn.close()
    logger.info("[migrate] 迁移完成")


if __name__ == "__main__":
    main()
