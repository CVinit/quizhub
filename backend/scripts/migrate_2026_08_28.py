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


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def migrate_exam_question_unique(conn: sqlite3.Connection) -> None:
    # 中断恢复：上一次迁移可能在 RENAME 后、回填前失败（例如索引名冲突），
    # 留下空的 exam_questions + 有数据的 exam_questions_old。此时必须先把数据
    # 搬回去，否则后续判断会误认为"已迁移完成"而永久丢数据。
    if _table_exists(conn, "exam_questions_old"):
        logger.warning("[migrate] 检测到中断的重建（exam_questions_old 残留），先恢复数据 ...")
        _recover_interrupted_rebuild(conn)

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
    # SQLite 的 RENAME 会把原表的索引一并带走并保留索引名，
    # 若在此处不删除，下面的 CREATE INDEX 会因"索引名已存在"失败，
    # 导致迁移中断在"已 RENAME、未回填"的危险状态（exam_questions 变空）。
    for idx in ("ix_exam_questions_exam_definition_id", "ix_exam_questions_question_id", "uq_exam_question"):
        if _table_has_index(conn, idx):
            conn.execute(f"DROP INDEX {idx}")
    conn.execute(
        """
        CREATE TABLE exam_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_definition_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            score FLOAT NOT NULL DEFAULT 2,
            shuffle_map JSON,
            CONSTRAINT fk_exam_def FOREIGN KEY (exam_definition_id) REFERENCES exam_definitions (id) ON DELETE CASCADE,
            CONSTRAINT fk_question FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE CASCADE
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
    # 必须先结束事务再重开外键：`PRAGMA foreign_keys` 在事务内执行是**静默 no-op**
    # （实测：事务内执行后 pragma 仍为 0），会让上面的 OFF 延续到连接结束，
    # 之后任何写操作都失去外键校验。migrate_2026_09_16.py 也是先 commit 再重开。
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    logger.info("[migrate] uq_exam_question 已创建")


def _recover_interrupted_rebuild(conn: sqlite3.Connection) -> None:
    """从"已 RENAME 但未回填"的中断状态恢复 exam_questions 数据。

    中断特征：exam_questions 为空（或不存在）而 exam_questions_old 有数据。
    恢复策略：以 old 表为准重建目标表并回填，最后删除 old 表。
    """
    old_count = conn.execute("SELECT COUNT(*) FROM exam_questions_old").fetchone()[0]
    new_count = (
        conn.execute("SELECT COUNT(*) FROM exam_questions").fetchone()[0]
        if _table_exists(conn, "exam_questions")
        else 0
    )
    if new_count >= old_count and new_count > 0:
        # 目标表已有完整数据，仅需清理残留 old 表
        conn.execute("DROP TABLE exam_questions_old")
        conn.commit()
        logger.info("[migrate] exam_questions 数据完整（%d 行），已清理残留旧表", new_count)
        return

    logger.warning("[migrate] 检测到数据未回填（新表 %d 行 < 旧表 %d 行），正在恢复 ...", new_count, old_count)
    conn.execute("PRAGMA foreign_keys=OFF")
    if _table_exists(conn, "exam_questions"):
        conn.execute("DROP TABLE exam_questions")
    conn.execute("ALTER TABLE exam_questions_old RENAME TO exam_questions")
    # RENAME 会把 old 表上的索引带过来并保留名字，必须先删掉同名索引再新建
    for idx in ("ix_exam_questions_exam_definition_id", "ix_exam_questions_question_id", "uq_exam_question"):
        if _table_has_index(conn, idx):
            conn.execute(f"DROP INDEX {idx}")
    conn.execute("CREATE INDEX ix_exam_questions_exam_definition_id ON exam_questions (exam_definition_id)")
    conn.execute("CREATE INDEX ix_exam_questions_question_id ON exam_questions (question_id)")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    restored = conn.execute("SELECT COUNT(*) FROM exam_questions").fetchone()[0]
    logger.info("[migrate] 已恢复 exam_questions：%d 行", restored)


def migrate_legacy_plain_settings(conn: sqlite3.Connection) -> None:
    """清理历史遗留的 "plain:" 前缀敏感设置值。

    旧版 encrypt_value 在未配置 TRAINING_ENC_KEY 时回退写入 "plain:"+base64(value)
    却仍以 encrypted=1 入库（见 docs/audit_report.md SEC-P1-6）。该回退已被移除，
    decrypt_value 现在遇到 "plain:" 会直接抛 RuntimeError —— 若库中残留此类行，
    GET /system/settings（尤其 category=smtp）会整体 500，管理端无法打开邮件设置。

    迁移策略：敏感设置无法安全还原为密文（且旧回退本身不安全），因此
    一律清空其值并置 encrypted=0：(key, value, encrypted) -> (key, '', 0)。
    密码等敏感项被清空后需管理员重新填写，这是安全的默认行为。
    """
    rows = conn.execute("SELECT key, value FROM settings WHERE value LIKE 'plain:%'").fetchall()
    if not rows:
        logger.info("[migrate] 未发现遗留 plain: 敏感设置，跳过")
        return
    keys = [r[0] for r in rows]
    conn.execute("UPDATE settings SET value = '', encrypted = 0 WHERE value LIKE 'plain:%'")
    conn.commit()
    logger.info("[migrate] 已清空 %d 个遗留 plain: 设置项：%s（需管理员重新填写）", len(keys), ", ".join(keys))


def main() -> None:
    # 刻意不声明任何选项：本脚本没有 dry-run 实现，任何多余参数（例如误传 --dry-run）
    # 都必须立即报错退出，而不是被静默忽略后**真的执行迁移**。原实现不解析参数，
    # 运维以为在预演、实际已开始重建 exam_questions。
    argparse.ArgumentParser(description="历史增量迁移（overtime / token_version / 唯一约束 / 遗留设置）").parse_args()

    if not DB_PATH.exists():
        logger.error("[migrate] 数据库不存在：%s，请先运行 init_db.py", DB_PATH)
        sys.exit(1)
    # 本脚本会重建 exam_questions（DROP/RENAME）等破坏性操作，必须先留可回滚的备份；
    # backup_database 同时把自动备份数量收敛到上限，避免每次启动都堆积。
    backup_database(DB_PATH)
    logger.info("[migrate] 开始迁移：%s", DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        migrate_overtime(conn)
        migrate_token_version(conn)
        migrate_verification_attempts(conn)
        migrate_exam_question_unique(conn)
        migrate_active_session_index(conn)
        migrate_legacy_plain_settings(conn)
    except Exception as exc:  # noqa: BLE001  迁移脚本需给出明确失败信号
        logger.exception("[migrate] 迁移失败：%s", exc)
        sys.exit(1)
    finally:
        conn.close()
    logger.info("[migrate] 迁移完成")


if __name__ == "__main__":
    main()
