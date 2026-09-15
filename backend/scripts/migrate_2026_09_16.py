"""一次性迁移：邮箱大小写归一 + NOCASE 排序规则 + 外键级联规则。

对应 2026-09 后端安全/数据完整性修复，仅适用于**已存在既有数据**的 SQLite 库；
新库经 init_db 建表即包含全部定义，无需运行本脚本。

变更内容：
1. users.email 归一为 `strip().lower()`，消除大小写变体导致的重复账号
   （`Admin@x.com` 与 `admin@x.com` 曾可同时存在，且用户无法用另一种大小写登录）。
2. users.email 重建为 `COLLATE NOCASE`，作为二次防线。
3. 为原先缺失 `ON DELETE` 规则的外键补齐级联行为，修复
   「删除有练习/考试记录的用户或题目时报 FOREIGN KEY constraint failed → HTTP 500」。
   SQLite 无法直接 ALTER 外键，需按官方推荐做「建新表 → 拷数据 → 换名」。

用法：
    uv run python scripts/migrate_2026_09_16.py            # 执行全部迁移（先备份）
    uv run python scripts/migrate_2026_09_16.py --dry-run  # 仅报告将要执行的变更
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("quizhub.migrate")

# 需要重建以补齐 ON DELETE 规则的表，按依赖顺序（子表在前，减少重建期约束冲突）。
# 每项为 (表名, 建表 DDL)：DDL 与 app/models 中的当前定义保持一致。
_TABLE_DDL: dict[str, str] = {
    "practice_records": """
        CREATE TABLE practice_records (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            question_id INTEGER NOT NULL REFERENCES questions (id) ON DELETE CASCADE,
            bank_id INTEGER REFERENCES question_banks (id) ON DELETE SET NULL,
            mode VARCHAR NOT NULL,
            user_answer JSON,
            is_correct BOOLEAN,
            self_eval BOOLEAN,
            answered_at VARCHAR NOT NULL
        )
    """,
    "question_states": """
        CREATE TABLE question_states (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            question_id INTEGER NOT NULL REFERENCES questions (id) ON DELETE CASCADE,
            status VARCHAR NOT NULL,
            marked BOOLEAN NOT NULL,
            marked_note VARCHAR NOT NULL,
            answered_at VARCHAR,
            CONSTRAINT uq_user_question_state UNIQUE (user_id, question_id)
        )
    """,
    "exam_sessions": """
        CREATE TABLE exam_sessions (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            exam_definition_id INTEGER NOT NULL REFERENCES exam_definitions (id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            status VARCHAR NOT NULL,
            answers JSON NOT NULL,
            version INTEGER NOT NULL,
            started_at VARCHAR NOT NULL,
            submitted_at VARCHAR,
            remaining_sec INTEGER,
            created_at VARCHAR NOT NULL
        )
    """,
    "exam_results": """
        CREATE TABLE exam_results (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            exam_definition_id INTEGER NOT NULL REFERENCES exam_definitions (id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            exam_session_id INTEGER REFERENCES exam_sessions (id) ON DELETE CASCADE,
            score FLOAT NOT NULL,
            total_score FLOAT NOT NULL,
            passed BOOLEAN NOT NULL,
            correct_count INTEGER NOT NULL,
            total_count INTEGER NOT NULL,
            objective_score FLOAT NOT NULL,
            need_review BOOLEAN NOT NULL,
            published BOOLEAN NOT NULL,
            overtime BOOLEAN NOT NULL DEFAULT 0,
            created_at VARCHAR NOT NULL
        )
    """,
    "short_answer_reviews": """
        CREATE TABLE short_answer_reviews (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            exam_result_id INTEGER NOT NULL REFERENCES exam_results (id) ON DELETE CASCADE,
            exam_session_id INTEGER REFERENCES exam_sessions (id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            question_id INTEGER NOT NULL REFERENCES questions (id) ON DELETE CASCADE,
            user_answer TEXT NOT NULL,
            reference_answer TEXT NOT NULL,
            verdict VARCHAR,
            partial_score FLOAT,
            reviewer INTEGER REFERENCES users (id) ON DELETE SET NULL,
            reviewed_at VARCHAR
        )
    """,
    "exam_questions": """
        CREATE TABLE exam_questions (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            exam_definition_id INTEGER NOT NULL REFERENCES exam_definitions (id) ON DELETE CASCADE,
            question_id INTEGER NOT NULL REFERENCES questions (id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            score FLOAT NOT NULL,
            shuffle_map JSON,
            CONSTRAINT uq_exam_question UNIQUE (exam_definition_id, question_id)
        )
    """,
    "user_groups": """
        CREATE TABLE user_groups (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            group_id INTEGER NOT NULL REFERENCES groups (id) ON DELETE CASCADE,
            CONSTRAINT uq_user_group UNIQUE (user_id, group_id)
        )
    """,
    "drafts": """
        CREATE TABLE drafts (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            form_key VARCHAR NOT NULL,
            payload JSON NOT NULL,
            updated_at VARCHAR NOT NULL,
            CONSTRAINT uq_user_draft UNIQUE (user_id, form_key)
        )
    """,
    "audit_logs": """
        CREATE TABLE audit_logs (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            actor INTEGER REFERENCES users (id) ON DELETE SET NULL,
            action VARCHAR NOT NULL,
            target_type VARCHAR NOT NULL,
            target_id VARCHAR NOT NULL,
            detail JSON,
            ip VARCHAR NOT NULL,
            created_at VARCHAR NOT NULL
        )
    """,
    "stats_user_daily": """
        CREATE TABLE stats_user_daily (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            date VARCHAR NOT NULL,
            group_id INTEGER REFERENCES groups (id) ON DELETE SET NULL,
            answer_count INTEGER NOT NULL,
            correct_count INTEGER NOT NULL,
            wrong_count INTEGER NOT NULL,
            exam_count INTEGER NOT NULL,
            exam_score_sum FLOAT NOT NULL,
            exam_pass_count INTEGER NOT NULL,
            CONSTRAINT uq_user_daily UNIQUE (user_id, date, group_id)
        )
    """,
}

# 重建后需要恢复的索引：(索引名, 表, 列定义)
_INDEXES: list[tuple[str, str, str]] = [
    ("ix_practice_records_user_id", "practice_records", "user_id"),
    ("ix_practice_records_question_id", "practice_records", "question_id"),
    ("ix_practice_records_answered_at", "practice_records", "answered_at"),
    ("ix_question_states_question_id", "question_states", "question_id"),
    ("ix_exam_sessions_exam_definition_id", "exam_sessions", "exam_definition_id"),
    ("ix_exam_sessions_user_id", "exam_sessions", "user_id"),
    ("ix_exam_sessions_status", "exam_sessions", "status"),
    ("ix_exam_results_exam_definition_id", "exam_results", "exam_definition_id"),
    ("ix_exam_results_user_id", "exam_results", "user_id"),
    ("ix_exam_results_exam_session_id", "exam_results", "exam_session_id"),
    ("ix_exam_results_published", "exam_results", "published"),
    ("ix_short_answer_reviews_exam_result_id", "short_answer_reviews", "exam_result_id"),
    ("ix_short_answer_reviews_user_id", "short_answer_reviews", "user_id"),
    ("ix_exam_questions_exam_definition_id", "exam_questions", "exam_definition_id"),
    ("ix_exam_questions_question_id", "exam_questions", "question_id"),
    ("ix_user_groups_user_id", "user_groups", "user_id"),
    ("ix_user_groups_group_id", "user_groups", "group_id"),
    ("ix_audit_logs_actor", "audit_logs", "actor"),
    ("ix_audit_logs_action", "audit_logs", "action"),
    ("ix_stats_user_daily_user_id", "stats_user_daily", "user_id"),
    ("ix_stats_user_daily_date", "stats_user_daily", "date"),
    ("ix_stats_user_daily_group_id", "stats_user_daily", "group_id"),
    ("ix_stats_date_user", "stats_user_daily", "date, user_id"),
]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _has_ondelete(conn: sqlite3.Connection, table: str) -> bool:
    """判断某表的建表 SQL 是否已包含 ON DELETE 规则。"""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row and row[0] and "ON DELETE" in row[0].upper())


def migrate_normalize_emails(conn: sqlite3.Connection, dry_run: bool) -> int:
    """把 users.email 归一为小写并处理归一后产生的重复账号。

    Returns:
        被归一（值发生变化）的行数。
    """
    rows = conn.execute("SELECT id, email FROM users").fetchall()
    changed = [(rid, email) for rid, email in rows if email and email != email.strip().lower()]
    if not changed:
        logger.info("[migrate] users.email 无需归一，跳过")
        return 0
    logger.info("[migrate] 发现 %d 个非归一邮箱，将转为小写", len(changed))

    # 归一后可能撞唯一约束（Admin@x.com 与 admin@x.com 并存）：
    # 保留 id 最小者，其余账号改名加后缀以保证可登录且数据不丢，交由管理员人工合并。
    seen: dict[str, int] = {}
    for rid, email in sorted(rows, key=lambda r: r[0]):
        low = (email or "").strip().lower()
        if not low:
            continue
        if low in seen:
            new_email = f"{low}.dup{rid}"
            logger.warning("[migrate] 邮箱冲突：id=%s 的 %s 与 id=%s 重复，改名为 %s", rid, email, seen[low], new_email)
            if not dry_run:
                conn.execute("UPDATE users SET email=? WHERE id=?", (new_email, rid))
        else:
            seen[low] = rid

    if not dry_run:
        for rid, email in changed:
            conn.execute("UPDATE users SET email=? WHERE id=?", (email.strip().lower(), rid))
        conn.commit()
    return len(changed)


def rebuild_table_with_ondelete(conn: sqlite3.Connection, table: str, ddl: str, dry_run: bool) -> bool:
    """按 SQLite 官方推荐流程重建表以补齐外键 ON DELETE 规则。"""
    if not _table_exists(conn, table):
        logger.info("[migrate] 表 %s 不存在，跳过", table)
        return False
    if _has_ondelete(conn, table):
        logger.info("[migrate] 表 %s 已含 ON DELETE 规则，跳过", table)
        return False
    if dry_run:
        logger.info("[migrate] [dry-run] 将重建表 %s 以补齐外键级联规则", table)
        return True

    logger.info("[migrate] 重建表 %s 以补齐外键级联规则 ...", table)
    tmp = f"{table}__new"
    conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    conn.execute(ddl.replace(f"CREATE TABLE {table}", f"CREATE TABLE {tmp}", 1))

    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    shared = [c for c in cols if c in {r[1] for r in conn.execute(f"PRAGMA table_info({tmp})").fetchall()}]
    collist = ", ".join(f'"{c}"' for c in shared)
    conn.execute(f'INSERT INTO {tmp} ({collist}) SELECT {collist} FROM {table}')
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")

    for name, tbl, coldef in _INDEXES:
        if tbl == table:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({coldef})")
    conn.commit()
    logger.info("[migrate] 表 %s 重建完成", table)
    return True


def purge_orphans(conn: sqlite3.Connection, dry_run: bool) -> int:
    """清理历史遗留的悬空外键行。

    补齐 ON DELETE 规则后，原先「无外键约束」的表（如 stats_user_daily）会暴露出
    既有脏数据（典型来源：手工测试插入的 user_id、已删除用户的残留行）。
    这些行在新约束下无法通过 foreign_key_check，且指向不存在的用户，
    属于不可展示的死数据，直接删除；有业务价值者（如审计日志）改为置空。

    Returns:
        被清理的行数。
    """
    # 需要保留记录本身的表：置空外键而非删除
    nullify: list[tuple[str, str]] = [("audit_logs", "actor"), ("stats_user_daily", "group_id")]
    # 依赖用户的业务数据：外键失效则整行无意义，删除
    cascade: list[tuple[str, str]] = [
        ("practice_records", "user_id"),
        ("practice_records", "question_id"),
        ("question_states", "user_id"),
        ("question_states", "question_id"),
        ("exam_sessions", "user_id"),
        ("exam_sessions", "exam_definition_id"),
        ("exam_results", "user_id"),
        ("exam_results", "exam_definition_id"),
        ("exam_results", "exam_session_id"),
        ("short_answer_reviews", "user_id"),
        ("short_answer_reviews", "exam_result_id"),
        ("exam_questions", "exam_definition_id"),
        ("exam_questions", "question_id"),
        ("user_groups", "user_id"),
        ("user_groups", "group_id"),
        ("drafts", "user_id"),
        ("stats_user_daily", "user_id"),
    ]
    parents = {
        "user_id": "users",
        "actor": "users",
        "reviewer": "users",
        "question_id": "questions",
        "exam_definition_id": "exam_definitions",
        "exam_session_id": "exam_sessions",
        "exam_result_id": "exam_results",
        "group_id": "groups",
    }

    cleaned = 0
    for table, col in cascade:
        parent = parents.get(col)
        if not parent or not _table_exists(conn, table):
            continue
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL AND {col} NOT IN (SELECT id FROM {parent})"
        ).fetchone()[0]
        if n:
            logger.warning("[migrate] %s.%s 有 %d 行指向不存在的 %s，将被删除", table, col, n, parent)
            if not dry_run:
                conn.execute(f"DELETE FROM {table} WHERE {col} IS NOT NULL AND {col} NOT IN (SELECT id FROM {parent})")
            cleaned += n

    for table, col in nullify:
        parent = parents.get(col)
        if not parent or not _table_exists(conn, table):
            continue
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL AND {col} NOT IN (SELECT id FROM {parent})"
        ).fetchone()[0]
        if n:
            logger.warning("[migrate] %s.%s 有 %d 行指向不存在的 %s，将置空（保留记录）", table, col, n, parent)
            if not dry_run:
                conn.execute(f"UPDATE {table} SET {col}=NULL WHERE {col} IS NOT NULL AND {col} NOT IN (SELECT id FROM {parent})")
            cleaned += n

    if not dry_run and cleaned:
        conn.commit()
    return cleaned


def migrate_email_collation(conn: sqlite3.Connection, dry_run: bool) -> None:
    """把 users 表重建为 email COLLATE NOCASE。"""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    if not row or not row[0]:
        logger.info("[migrate] users 表不存在，跳过")
        return
    if "NOCASE" in row[0].upper():
        logger.info("[migrate] users.email 已为 NOCASE，跳过")
        return
    if dry_run:
        logger.info("[migrate] [dry-run] 将重建 users 表以启用 email COLLATE NOCASE")
        return
    logger.info("[migrate] 重建 users 表以启用 email COLLATE NOCASE ...")
    old_sql = row[0]
    new_sql = old_sql.replace("email VARCHAR NOT NULL", "email VARCHAR NOT NULL COLLATE NOCASE", 1)
    if "COLLATE NOCASE" not in new_sql:
        # 兜底：按已知定义重建
        new_sql = (
            "CREATE TABLE users__new ("
            "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,"
            "email VARCHAR NOT NULL COLLATE NOCASE,"
            "password_hash VARCHAR NOT NULL,"
            "name VARCHAR NOT NULL,"
            "role VARCHAR NOT NULL,"
            "status VARCHAR NOT NULL,"
            "email_verified BOOLEAN NOT NULL,"
            "token_version INTEGER NOT NULL,"
            "dept_group_id INTEGER REFERENCES groups (id) ON DELETE SET NULL,"
            "created_at VARCHAR NOT NULL,"
            "UNIQUE (email))"
        )
        conn.execute("DROP TABLE IF EXISTS users__new")
        conn.execute(new_sql)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        collist = ", ".join(f'"{c}"' for c in cols)
        conn.execute(f"INSERT INTO users__new ({collist}) SELECT {collist} FROM users")
        conn.execute("DROP TABLE users")
        conn.execute("ALTER TABLE users__new RENAME TO users")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)")
        conn.commit()
        logger.info("[migrate] users 表重建完成（NOCASE）")
        return
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute("UPDATE sqlite_master SET sql=? WHERE type='table' AND name='users'", (new_sql,))
    conn.execute("PRAGMA writable_schema=OFF")
    conn.commit()
    logger.info("[migrate] users.email 已切换为 NOCASE（schema 级修改）")


def main() -> None:
    parser = argparse.ArgumentParser(description="邮箱归一 + 外键级联规则迁移")
    parser.add_argument("--dry-run", action="store_true", help="只报告将执行的变更，不写入数据库")
    args = parser.parse_args()

    if not DB_PATH.exists():
        logger.error("[migrate] 数据库不存在：%s，请先运行 init_db.py", DB_PATH)
        sys.exit(1)

    if not args.dry_run:
        backup = DB_PATH.with_suffix(f".db.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(DB_PATH, backup)
        logger.info("[migrate] 已备份数据库到 %s", backup)

    logger.info("[migrate] 开始迁移：%s", DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        # 重建期间关闭外键检查，避免 DROP/RENAME 顺序触发约束错误
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("PRAGMA legacy_alter_table=ON")
        normalized = migrate_normalize_emails(conn, args.dry_run)
        migrate_email_collation(conn, args.dry_run)
        rebuilt = 0
        for table, ddl in _TABLE_DDL.items():
            if rebuild_table_with_ondelete(conn, table, ddl, args.dry_run):
                rebuilt += 1
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")
        # 补齐约束后清理既有悬空引用，否则 foreign_key_check 必然报错
        purged = purge_orphans(conn, args.dry_run)
        integrity = conn.execute("PRAGMA foreign_key_check").fetchall()
        if integrity:
            logger.error("[migrate] 外键一致性检查仍未通过，共 %d 处：%s", len(integrity), integrity[:10])
            logger.error("[migrate] 数据库已备份，可回滚；请人工核查上述表后再重试")
            sys.exit(2)
        logger.info(
            "[migrate] 迁移完成：归一邮箱 %d 行，重建表 %d 张，清理悬空引用 %d 行，外键一致性检查通过",
            normalized,
            rebuilt,
            purged,
        )
    except Exception as exc:  # noqa: BLE001  迁移脚本需给出明确失败信号
        logger.exception("[migrate] 迁移失败：%s", exc)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
