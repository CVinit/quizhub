"""统计公共工具：业务时区、UTC 时刻与「业务日 → UTC 区间」换算。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone, tzinfo

from app.core.timeutil import business_tz

logger = logging.getLogger("quizhub")

# 业务时区直接复用 core.timeutil 的单一实现：两处各自 ZoneInfo + 回退会让
# 「统计按日归属」与「考试时段解析」在时区名非法时出现一个回退 UTC、一个不回退的分歧。
_TZ: tzinfo = business_tz()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _date_str(dt: datetime) -> str:
    """把某个时刻换算到业务时区后的本地日期 YYYY-MM-DD。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ).strftime("%Y-%m-%d")


def _day_bounds_utc(date_str: str) -> tuple[str, str]:
    """把业务时区的某一天换算成 UTC 的 [起, 止) ISO 字符串区间。

    返回的边界用于对存储为 UTC ISO 字符串的列做**范围比较**，
    而不是在 SQL 里调用 datetime()/date() 解析字符串：
    SQLite 的 datetime() 对无法识别的格式（如带 Z 后缀、空格分隔）返回 NULL，
    会让整个 WHERE 恒假、统计静默归零，且函数包裹列会使索引失效。
    范围比较既健壮又能命中 answered_at / created_at 上的索引。

    ISO-8601 在统一使用 `+00:00` 偏移且位数固定的前提下，字典序等价于时间序。
    """
    local_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_TZ)
    start_utc = local_start.astimezone(timezone.utc)
    end_utc = (local_start + timedelta(days=1)).astimezone(timezone.utc)
    return start_utc.isoformat(), end_utc.isoformat()
