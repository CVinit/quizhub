"""数据库备份工具（供 `scripts/migrate_*.py` 使用）。

背景：迁移脚本在**每次应用启动**时都会被全部执行（`docker/entrypoint.sh`、`start.sh`、
`start.bat`、`start.ps1`），而原来每个脚本都无条件完整复制一份 SQLite 数据库且从不清理。
配合 `restart: unless-stopped` 的容器，备份文件会无界增长，最终撑爆磁盘。

这里统一提供 `backup_database()`：用 SQLite 在线备份 API 生成一份带时间戳的一致快照
（WAL 模式下直接复制主文件会丢失未 checkpoint 的事务），并把**自动生成的**备份数量
收敛到上限（默认保留最近 5 份）。只清理严格匹配 `.db.bak-<14 位时间戳>` 的文件，
人工命名的备份（如 `.db.bak-before-repair`）一律不动。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("quizhub")

DEFAULT_BACKUP_KEEP = 5

# 仅匹配本模块生成的备份名：<db>.bak-YYYYmmddHHMMSS。人工命名的备份不匹配，因此不会被清理。
_BACKUP_RE = re.compile(r"\.db\.bak-\d{14}$")


def backup_database(db_path: Path, *, keep: int = DEFAULT_BACKUP_KEEP) -> Path | None:
    """备份 SQLite 数据库，并把过量的历史自动备份清理掉。

    Args:
        db_path: 数据库文件路径。
        keep: 自动备份最多保留的份数（最小按 1 处理）。

    Returns:
        备份文件路径；数据库不存在、或备份完整性校验失败时返回 None
        （后者不清理既有备份，避免坏备份挤掉更早的完整备份）。
    """
    if not db_path.exists():
        return None
    backup = db_path.with_suffix(f".db.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    # 必须用 SQLite 在线备份 API，不能只复制主库文件：本库启用了 WAL
    # （database.py 的 `PRAGMA journal_mode=WAL`，且该属性持久化在库文件中），
    # 已提交但未 checkpoint 的事务只存在于 -wal 边车文件里（进程被 kill 后是常态），
    # 单纯 shutil.copy2 只能得到丢失最新数据的备份。
    if not _sqlite_backup(db_path, backup):
        return None
    logger.info("[migrate] 已备份数据库到 %s", backup)
    prune_backups(db_path, keep=keep)
    return backup


def _sqlite_backup(src_path: Path, dst_path: Path) -> bool:
    """用 SQLite 备份 API 生成一致性快照，并校验其完整可读。

    校验失败的备份会被删除并返回 False —— 否则 prune_backups 会把这份坏备份当作
    “最新备份”保留，反而删掉更早的完整备份。

    Args:
        src_path: 源数据库路径。
        dst_path: 目标备份路径。

    Returns:
        备份是否成功且通过 `PRAGMA integrity_check`。
    """
    dst_path.unlink(missing_ok=True)
    try:
        src = sqlite3.connect(str(src_path))
        try:
            dst = sqlite3.connect(str(dst_path))
            try:
                with dst:
                    src.backup(dst)
                row = dst.execute("PRAGMA integrity_check").fetchone()
            finally:
                dst.close()
        finally:
            src.close()
    except (sqlite3.Error, OSError) as exc:
        # 源不是合法 SQLite 库（或磁盘/权限异常）：丢弃半成品并降级返回 False，
        # 不向上抛异常——调用方（启动迁移）不应因一次备份失败而无法启动。
        logger.error("[migrate] 备份数据库失败（%s）：%s", src_path, exc)
        dst_path.unlink(missing_ok=True)
        return False
    if row != ("ok",):
        logger.error("[migrate] 备份完整性校验失败（%s），已丢弃该备份：%s", dst_path, row)
        dst_path.unlink(missing_ok=True)
        return False
    return True


def prune_backups(db_path: Path, *, keep: int = DEFAULT_BACKUP_KEEP) -> list[Path]:
    """删除最旧的自动备份，只保留最新的 `keep` 份。

    Args:
        db_path: 数据库文件路径。
        keep: 保留份数（最小按 1 处理）。

    Returns:
        被删除的备份文件路径列表。
    """
    keep = max(1, keep)
    candidates = sorted(
        (p for p in db_path.parent.glob(f"{db_path.name}.bak-*") if _BACKUP_RE.search(p.name)),
        key=lambda p: p.name,  # 时间戳定宽零填充，字典序即时间序
    )
    removed: list[Path] = []
    for stale in candidates[:-keep]:
        try:
            stale.unlink()
        except OSError:
            logger.warning("[migrate] 旧备份清理失败，已跳过：%s", stale)
            continue
        removed.append(stale)
    if removed:
        logger.info("[migrate] 已清理 %d 份过旧备份（保留最近 %d 份）", len(removed), keep)
    return removed
