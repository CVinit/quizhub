"""一次性迁移：清理已移除的遗留设置行。

背景（2026-09-24 后端审查）：
- `max_questions_per_exam`（管理端「单场最大题数」）在全仓没有任何读取方 —— 真正生效的是
  试卷模板自身的 `config.max_questions`（paper_service）。它表现为一个「改了没有任何效果」
  的误导性开关，已从 `DEFAULT_SETTINGS` / 标签表 / 校验分支中移除。
- `settings` 表可能仍留着该行：`get_settings` 是「读全表」，孤儿行会让管理端设置页出现
  一个既无标签、也无法保存的字段。

本脚本删除这些行（幂等）；新库经 init_db 建表本就不含它们。
start.sh / 容器 entrypoint 会按文件名顺序自动执行本脚本。

用法：
    uv run python scripts/migrate_2026_09_24.py            # 执行迁移（先备份）
    uv run python scripts/migrate_2026_09_24.py --dry-run  # 仅报告将执行的变更
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
# 已从 DEFAULT_SETTINGS 移除的设置项 key（每个都应注明移除原因，便于回溯）
_RETIRED_KEYS = ("max_questions_per_exam",)


def count_retired_rows(conn: sqlite3.Connection) -> int:
    """返回仍存在的遗留设置行数。

    Args:
        conn: SQLite 连接。

    Returns:
        命中 `_RETIRED_KEYS` 的行数（表不存在时返回 0）。
    """
    exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (_SETTINGS_TABLE,)).fetchone()
    if not exists:
        return 0
    placeholders = ",".join("?" for _ in _RETIRED_KEYS)
    row = conn.execute(
        f"SELECT COUNT(*) FROM {_SETTINGS_TABLE} WHERE key IN ({placeholders})",  # noqa: S608  表名/占位符均为本模块常量
        _RETIRED_KEYS,
    ).fetchone()
    return int(row[0]) if row else 0


def remove_retired_settings(conn: sqlite3.Connection) -> int:
    """删除遗留设置行，返回删除行数（幂等）。

    Args:
        conn: SQLite 连接。

    Returns:
        实际删除的行数。
    """
    if count_retired_rows(conn) == 0:
        return 0
    placeholders = ",".join("?" for _ in _RETIRED_KEYS)
    cursor = conn.execute(
        f"DELETE FROM {_SETTINGS_TABLE} WHERE key IN ({placeholders})",  # noqa: S608  同上
        _RETIRED_KEYS,
    )
    return int(cursor.rowcount or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="清理已移除的遗留设置行")
    parser.add_argument("--dry-run", action="store_true", help="只报告将执行的变更，不写入数据库")
    args = parser.parse_args()

    if not DB_PATH.exists():
        logger.error("[migrate] 数据库不存在：%s，请先运行 init_db.py", DB_PATH)
        sys.exit(1)

    if not args.dry_run:
        # 备份 + 清理过旧备份：迁移每次启动都会跑，无上限的备份会撑爆磁盘
        backup_database(DB_PATH)

    logger.info("[migrate] 开始清理已移除的遗留设置：%s", DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        pending = count_retired_rows(conn)
        if not pending:
            logger.info("[migrate] 未发现遗留设置行（%s），无需处理", ", ".join(_RETIRED_KEYS))
            return
        if args.dry_run:
            logger.info("[migrate] dry-run：将删除 %d 行遗留设置（%s）", pending, ", ".join(_RETIRED_KEYS))
            return
        removed = remove_retired_settings(conn)
        conn.commit()
        logger.info("[migrate] 完成：已删除 %d 行遗留设置", removed)
    except Exception as exc:  # noqa: BLE001  迁移脚本需给出明确失败信号
        logger.exception("[migrate] 迁移失败：%s", exc)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
