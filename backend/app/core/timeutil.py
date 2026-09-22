"""统一 UTC 时间戳口径。

各服务原先各自实现 `datetime.now(timezone.utc).isoformat()`。一旦某处格式漂移
（例如漏掉 `+00:00` 偏移），所有按字符串比较的查询（统计按日归属、审计时间筛选）
就会静默错位。收敛到本模块，保证全站只有一种 UTC ISO 表示。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo

from app.config import BUSINESS_TZ

logger = logging.getLogger("quizhub")

# 业务时区（TRAINING_TZ，默认 Asia/Shanghai）。未知时区名回退 UTC，避免启动即崩溃。
try:
    _BUSINESS_TZ: tzinfo = ZoneInfo(BUSINESS_TZ)
except Exception:  # noqa: BLE001  时区库缺失或名称非法时降级
    logger.warning("[timeutil] 无法加载时区 %s，已回退 UTC", BUSINESS_TZ)
    _BUSINESS_TZ = timezone.utc


def utcnow_iso() -> str:
    """返回带 `+00:00` 偏移的 UTC ISO-8601 时间戳。"""
    return datetime.now(timezone.utc).isoformat()


def business_tz() -> tzinfo:
    """返回业务时区（TRAINING_TZ，默认 Asia/Shanghai）。

    用于解析**不带偏移**的本地时间字符串（如管理端日期选择器提交的考试时段）。
    这类值表示业务本地时间而非 UTC，按 UTC 解析会让考试窗口整体偏移。
    """
    return _BUSINESS_TZ
