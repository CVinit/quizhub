"""一次性迁移：修复 settings.encrypted 标记与实际存储不一致的历史脏数据。

仅适用于**已存在既有数据**的 SQLite 库；新库经 init_db 建表即写入正确标记，
无需运行本脚本。start.sh / 容器 entrypoint 会按文件名顺序自动执行本脚本。

背景（线上表现：测试邮件与注册邮件都收不到）：
1. migrate_2026_08_28 清理遗留 "plain:" 行时把敏感项置为 `encrypted=0`；
2. 旧版 update_settings 更新已有行时只写 value，不回写 encrypted 标记；
3. 管理员随后在设置页重新保存 SMTP 密码，库里于是留下
   「value 为 `enc:` 密文、encrypted=0」的行；
4. 旧版 get_settings 只信标记位，把**密文本身**当明文密码交给 mail_service，
   SMTP 认证必然失败；失败又发生在后台任务里被吞掉，界面却提示"已发送"。

本脚本把标记位与值前缀对齐：
- `enc:` 前缀行  -> encrypted=1（可正常解密）；
- 其余非空且被标为加密的行 -> encrypted=0（存量明文，decrypt_value 原样返回）；
- 遗留 `plain:` 行 -> 清空并置 0（与 migrate_2026_08_28 一致，幂等兜底）。

用法：
    uv run python scripts/migrate_2026_09_17.py            # 执行迁移（先备份）
    uv run python scripts/migrate_2026_09_17.py --dry-run  # 仅报告将执行的变更
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


def repair_encrypted_flags(conn: sqlite3.Connection, dry_run: bool) -> tuple[int, int, int]:
    """对齐 settings.encrypted 标记与值前缀。

    Returns:
        (清理的 plain: 行数, 置为加密的行数, 置为明文的行数)。
    """
    legacy = conn.execute("SELECT key FROM settings WHERE value LIKE 'plain:%'").fetchall()
    to_encrypt = conn.execute("SELECT key FROM settings WHERE value LIKE 'enc:%' AND encrypted = 0").fetchall()
    to_plain = conn.execute(
        "SELECT key FROM settings "
        "WHERE encrypted = 1 AND value != '' AND value NOT LIKE 'enc:%' AND value NOT LIKE 'plain:%'"
    ).fetchall()

    if legacy:
        logger.warning("[migrate] 发现 %d 个遗留 plain: 敏感项，将清空：%s", len(legacy), [r[0] for r in legacy])
    if to_encrypt:
        logger.info(
            "[migrate] 发现 %d 个「值是密文但标记为明文」的设置项，将置 encrypted=1：%s",
            len(to_encrypt),
            [r[0] for r in to_encrypt],
        )
    if to_plain:
        logger.info(
            "[migrate] 发现 %d 个「标记为加密但值非密文」的设置项，将置 encrypted=0：%s",
            len(to_plain),
            [r[0] for r in to_plain],
        )

    if dry_run:
        return len(legacy), len(to_encrypt), len(to_plain)

    if legacy:
        conn.execute("UPDATE settings SET value = '', encrypted = 0 WHERE value LIKE 'plain:%'")
    if to_encrypt:
        conn.execute("UPDATE settings SET encrypted = 1 WHERE value LIKE 'enc:%' AND encrypted = 0")
    if to_plain:
        conn.execute(
            "UPDATE settings SET encrypted = 0 "
            "WHERE encrypted = 1 AND value != '' AND value NOT LIKE 'enc:%' AND value NOT LIKE 'plain:%'"
        )
    conn.commit()
    return len(legacy), len(to_encrypt), len(to_plain)


def main() -> None:
    parser = argparse.ArgumentParser(description="修复 settings.encrypted 标记不一致")
    parser.add_argument("--dry-run", action="store_true", help="只报告将执行的变更，不写入数据库")
    args = parser.parse_args()

    if not DB_PATH.exists():
        logger.error("[migrate] 数据库不存在：%s，请先运行 init_db.py", DB_PATH)
        sys.exit(1)

    if not args.dry_run:
        # 备份 + 清理过旧备份：迁移每次启动都会跑，无上限的备份会撑爆磁盘
        backup_database(DB_PATH)

    logger.info("[migrate] 开始修复 settings 加密标记：%s", DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        has_settings = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
        if not has_settings:
            logger.info("[migrate] settings 表不存在，跳过")
            return
        legacy, encrypted, plain = repair_encrypted_flags(conn, args.dry_run)
        logger.info(
            "[migrate] 完成：清理遗留 plain: 行 %d，置 encrypted=1 %d 行，置 encrypted=0 %d 行",
            legacy,
            encrypted,
            plain,
        )
    except Exception as exc:  # noqa: BLE001  迁移脚本需给出明确失败信号
        logger.exception("[migrate] 迁移失败：%s", exc)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
