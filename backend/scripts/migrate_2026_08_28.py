"""一次性迁移：为既有 training.db 补上本轮审计新增的约束/列。

变更内容（与 models 当前定义对齐）：
1. exam_questions 增加唯一约束 uq_exam_question(exam_definition_id, question_id) ——
   防止首次固化题目的 check-then-insert 竞态产生重复行、重复计分。
2. exam_results 增加 overtime BOOLEAN NOT NULL DEFAULT 0 ——
   记录是否超时交卷，供 publish_results 保持"超时即不及格"语义。
3. users 增加 token_version INTEGER NOT NULL DEFAULT 0 ——
   支持修改密码后立即使旧 JWT 失效。
4. exam_sessions 增加同一用户单场考试的活动会话唯一索引。
5. email_verifications 增加 attempts INTEGER NOT NULL DEFAULT 0。

用法：uv run python scripts/migrate_2026_08_28.py
仅适用于已存在既有数据的 SQLite 库；新库经 init_db 建表已含这些定义，无需运行。

SQLite 不支持 ALTER TABLE ADD CONSTRAINT，故 exam_questions 的唯一约束通过重建表实现：
复制数据 → 删旧表 → 建新表（含唯一约束）→ 回填数据。
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("quizhub.migrate")


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in cols


def _table_has_index(conn: sqlite3.Connection, index_name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (index_name,)).fetchone())


def migrate_overtime(conn: sqlite3.Connection) -> None:
    if _table_has_column(conn, "exam_results", "overtime"):
        logger.info("[migrate] exam_results.overtime 已存在，跳过")
        return
    logger.info("[migrate] 为 exam_results 增加 overtime 列 ...")
    conn.execute("ALTER TABLE exam_results ADD COLUMN overtime BOOLEAN NOT NULL DEFAULT 0")
    conn.commit()
    logger.info("[migrate] exam_results.overtime 已添加（既有成绩默认 overtime=0）")


def migrate_token_version(conn: sqlite3.Connection) -> None:
    if _table_has_column(conn, "users", "token_version"):
        logger.info("[migrate] users.token_version 已存在，跳过")
        return
    conn.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    logger.info("[migrate] users.token_version 已添加")


def migrate_verification_attempts(conn: sqlite3.Connection) -> None:
    if _table_has_column(conn, "email_verifications", "attempts"):
        logger.info("[migrate] email_verifications.attempts 已存在，跳过")
        return
    conn.execute("ALTER TABLE email_verifications ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    logger.info("[migrate] email_verifications.attempts 已添加")


def migrate_active_session_index(conn: sqlite3.Connection) -> None:
    if _table_has_index(conn, "uq_active_exam_session"):
        logger.info("[migrate] uq_active_exam_session 已存在，跳过")
        return
    conn.execute(
        "CREATE UNIQUE INDEX uq_active_exam_session "
        "ON exam_sessions (exam_definition_id, user_id) "
        "WHERE status IN ('in_progress', 'scoring')"
    )
    conn.commit()
    logger.info("[migrate] uq_active_exam_session 已创建")


def migrate_exam_question_unique(conn: sqlite3.Connection) -> None:
    if _table_has_index(conn, "uq_exam_question"):
        logger.info("[migrate] uq_exam_question 已存在，跳过")
        return

    # 检查是否已存在重复行（理论上不应有）：若有，迁移前需人工处理
    dups = conn.execute(
        "SELECT exam_definition_id, question_id, COUNT(*) c FROM exam_questions "
        "GROUP BY exam_definition_id, question_id HAVING c > 1"
    ).fetchall()
    if dups:
        logger.warning("[migrate] 发现重复 exam_questions 行，需人工清理后再迁移：%s", dups)
        sys.exit(1)

    logger.info("[migrate] 为 exam_questions 重建表以增加唯一约束 uq_exam_question ...")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("ALTER TABLE exam_questions RENAME TO exam_questions_old")
    conn.execute(
        """
        CREATE TABLE exam_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_definition_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            score FLOAT NOT NULL DEFAULT 2,
            shuffle_map JSON,
            CONSTRAINT fk_exam_def FOREIGN KEY (exam_definition_id) REFERENCES exam_definitions (id),
            CONSTRAINT fk_question FOREIGN KEY (question_id) REFERENCES questions (id)
        )
        """
    )
    conn.execute("CREATE INDEX ix_exam_questions_exam_definition_id ON exam_questions (exam_definition_id)")
    conn.execute("CREATE INDEX ix_exam_questions_question_id ON exam_questions (question_id)")
    conn.execute("CREATE UNIQUE INDEX uq_exam_question ON exam_questions (exam_definition_id, question_id)")
    conn.execute(
        "INSERT INTO exam_questions (id, exam_definition_id, question_id, seq, score, shuffle_map) "
        "SELECT id, exam_definition_id, question_id, seq, score, shuffle_map FROM exam_questions_old"
    )
    conn.execute("DROP TABLE exam_questions_old")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    logger.info("[migrate] uq_exam_question 已创建")


def main() -> None:
    if not DB_PATH.exists():
        logger.error("[migrate] 数据库不存在：%s，请先运行 init_db.py", DB_PATH)
        sys.exit(1)
    logger.info("[migrate] 开始迁移：%s", DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        migrate_overtime(conn)
        migrate_token_version(conn)
        migrate_verification_attempts(conn)
        migrate_exam_question_unique(conn)
        migrate_active_session_index(conn)
    finally:
        conn.close()
    logger.info("[migrate] 迁移完成")


if __name__ == "__main__":
    main()
