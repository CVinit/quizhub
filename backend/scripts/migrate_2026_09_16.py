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
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_PATH
from app.core.db_backup import backup_database

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

# users 表的 NOCASE 重建 DDL（列定义与 app/models/user.py 的当前定义一致）。
# 单独成常量而非复用 _TABLE_DDL：users 的重建时机（migrate_email_collation）先于
# 通用重建循环，且需要保留其原有索引。
_USERS_DDL_NOCASE = (
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

# 含 WHERE 子句的部分索引（_INDEXES 只能表达普通列清单，无法覆盖）。
# 重建 exam_sessions 会随 DROP TABLE 删除它，必须在重建后显式补回，否则
# 「同一用户同一考试仅一个进行中会话」的数据库级约束会永久丢失。
_PARTIAL_INDEX_DDL: dict[str, str] = {
    "exam_sessions": (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_exam_session "
        "ON exam_sessions (exam_definition_id, user_id) "
        "WHERE status IN ('in_progress', 'scoring')"
    ),
}

# 需要长期保留、但 reviewer 被删除后应置空的表（否则 foreign_key_check 失败中断迁移）
_NULLIFY_ORPHANS: list[tuple[str, str]] = [
    ("audit_logs", "actor"),
    ("stats_user_daily", "group_id"),
    ("short_answer_reviews", "reviewer"),
]


def _restore_partial_indexes(conn: sqlite3.Connection, table: str) -> None:
    """补回某表上的部分索引（重建只恢复 _INDEXES 中的普通索引）。"""
    ddl = _PARTIAL_INDEX_DDL.get(table)
    if ddl:
        conn.execute(ddl)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _has_ondelete(conn: sqlite3.Connection, table: str) -> bool:
    """判断某表的建表 SQL 是否已包含 ON DELETE 规则。"""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row and row[0] and "ON DELETE" in row[0].upper())


def migrate_normalize_emails(conn: sqlite3.Connection, dry_run: bool) -> int:
    """把 users.email 归一为小写并处理归一后产生的重复账号。

    归一后可能撞唯一约束（Admin@x.com 与 admin@x.com 并存）：保留 id 最小者，
    其余账号改名加 `.dup<id>` 后缀以保证可登录且数据不丢，交由管理员人工合并。

    实现要点：**一趟算出每行的最终值再写入**。原实现先对冲突行改名、随后又用一个
    遍历 `changed` 的循环把该行写回未加后缀的归一值，改名被覆盖 → 撞 ix_users_email
    → IntegrityError，使本迁移对它本要修复的数据永远无法完成（start.sh 的 set -e
    会让服务直接起不来）。

    Returns:
        被归一（值发生变化）的行数。

    Raises:
        RuntimeError: 归一后仍存在重复邮箱（自检失败，宁可中止也不留半迁移状态）。
    """
    rows = conn.execute("SELECT id, email FROM users ORDER BY id").fetchall()
    changed = [(rid, email) for rid, email in rows if email and email != email.strip().lower()]
    if not changed:
        logger.info("[migrate] users.email 无需归一，跳过")
        return 0
    logger.info("[migrate] 发现 %d 个非归一邮箱，将转为小写", len(changed))

    # 按 id 升序确定每行的最终邮箱；已被占用的值追加 .dup<id>（必要时继续追加序号）
    final: dict[int, str] = {}
    taken: set[str] = set()
    for rid, email in rows:
        low = (email or "").strip().lower()
        if not low:
            continue
        if low in taken:
            base = f"{low}.dup{rid}"
            logger.warning("[migrate] 邮箱冲突：id=%s 的 %r 与既有账号归一后重复，改名为 %s", rid, email, base)
            low = base
            suffix = 0
            while low in taken:
                suffix += 1
                low = f"{base}.{suffix}"
        taken.add(low)
        final[rid] = low

    if len(set(final.values())) != len(final):
        raise RuntimeError("邮箱归一后仍存在重复，已中止；请人工处理后重试")

    if dry_run:
        logger.info("[migrate] dry-run：将写入 %d 行最终邮箱", len(final))
        return len(changed)

    original = {rid: (email or "") for rid, email in rows}
    for rid, new_email in final.items():
        if original.get(rid) != new_email:
            conn.execute("UPDATE users SET email=? WHERE id=?", (new_email, rid))
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
    conn.execute(f"INSERT INTO {tmp} ({collist}) SELECT {collist} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")

    for name, tbl, coldef in _INDEXES:
        if tbl == table:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({coldef})")
    _restore_partial_indexes(conn, table)
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
    nullify: list[tuple[str, str]] = list(_NULLIFY_ORPHANS)
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
                conn.execute(
                    f"UPDATE {table} SET {col}=NULL WHERE {col} IS NOT NULL AND {col} NOT IN (SELECT id FROM {parent})"
                )
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
    # 必须重建表，不能只改 sqlite_master：SQLite 的索引在建立时按当时的列排序规则排列，
    # 只改列 collation 而不重建索引，既有索引会与新 collation 不一致（实测
    # `PRAGMA integrity_check` 报 "row N missing from index" / "non-unique entry in index"），
    # 邮箱唯一约束与等值查询因此不可靠。重建表会连同索引一起重建。
    existing_indexes = [
        r[0]
        for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='users' AND sql IS NOT NULL"
        ).fetchall()
    ]
    conn.execute("DROP TABLE IF EXISTS users__new")
    conn.execute(_USERS_DDL_NOCASE)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    new_cols = {r[1] for r in conn.execute("PRAGMA table_info(users__new)").fetchall()}
    # 只拷贝两侧共有的列：历史库若多出未知列，直接按列名全量 INSERT 会因列不存在而失败
    # （与 rebuild_table_with_ondelete 的 shared 口径一致，schema 以当前模型定义为准）。
    shared = [c for c in cols if c in new_cols]
    collist = ", ".join(f'"{c}"' for c in shared)
    conn.execute(f"INSERT INTO users__new ({collist}) SELECT {collist} FROM users")
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users__new RENAME TO users")
    # 原样重建迁移前已存在的索引（含 ix_users_email / ix_users_role / ix_users_status /
    # ix_users_dept_group_id）；自增索引 sql IS NULL，不在其中，由 UNIQUE 约束自动重建。
    for index_sql in existing_indexes:
        conn.execute(index_sql)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)")
    conn.commit()
    logger.info("[migrate] users 表重建完成（NOCASE），随表重建索引 %d 个", len(existing_indexes))


def main() -> None:
    parser = argparse.ArgumentParser(description="邮箱归一 + 外键级联规则迁移")
    parser.add_argument("--dry-run", action="store_true", help="只报告将执行的变更，不写入数据库")
    args = parser.parse_args()

    if not DB_PATH.exists():
        logger.error("[migrate] 数据库不存在：%s，请先运行 init_db.py", DB_PATH)
        sys.exit(1)

    if not args.dry_run:
        # 备份 + 清理过旧备份：迁移每次启动都会跑，无上限的备份会撑爆磁盘
        backup_database(DB_PATH)

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
        # 兜底：即使 exam_sessions 本次未重建（已含 ON DELETE），也确保部分唯一索引存在
        # （重建路径已在 rebuild_table_with_ondelete 内补回；此处覆盖历史库索引缺失的情况）。
        if not args.dry_run and _table_exists(conn, "exam_sessions"):
            _restore_partial_indexes(conn, "exam_sessions")
            conn.commit()
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")
        # 补齐约束后清理既有悬空引用，否则 foreign_key_check 必然报错
        purged = purge_orphans(conn, args.dry_run)
        integrity = conn.execute("PRAGMA foreign_key_check").fetchall()
        if integrity:
            logger.error("[migrate] 外键一致性检查仍未通过，共 %d 处：%s", len(integrity), integrity[:10])
            logger.error("[migrate] 数据库已备份，可回滚；请人工核查上述表后再重试")
            sys.exit(2)
        if (
            _table_exists(conn, "exam_sessions")
            and not conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_active_exam_session'"
            ).fetchone()
        ):
            logger.error("[migrate] uq_active_exam_session 缺失，单活跃会话约束未恢复")
            sys.exit(2)
        # 索引一致性检查：重建/改 collation 后若索引与表定义脱节，integrity_check 会
        # 报 "row N missing from index" / "non-unique entry in index"，必须视为迁移失败。
        index_integrity = conn.execute("PRAGMA integrity_check").fetchall()
        if index_integrity != [("ok",)]:
            logger.error("[migrate] 索引一致性检查未通过：%s", index_integrity[:10])
            logger.error("[migrate] 数据库已备份，可回滚；请人工核查后再重试")
            sys.exit(2)
        logger.info(
            "[migrate] 迁移完成：归一邮箱 %d 行，重建表 %d 张，清理悬空引用 %d 行，外键/索引一致性检查通过",
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
