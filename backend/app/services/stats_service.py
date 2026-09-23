"""统计对外入口（门面）。

实现按职责拆分在 `app.services.stats` 包内：
- `common`    业务时区与日期换算
- `aggregate` 每日预聚合（刷新今日/昨日/指定日期区间）
- `panel`     用户面板与管理端概览

本模块只做重导出，保持对 api 层与既有测试的原有导入路径不变。

注：排行榜（原 `stats/rank.py`）已于 2026-09-23 按产品决策整体下线，
相关重导出与 `rank_visible` 设置项一并移除。
"""

from __future__ import annotations

from app.services.stats.aggregate import (
    refresh_daily,
    refresh_for_timestamps,
    refresh_recent,
    refresh_user_daily,
    refresh_user_for_timestamps,
    startup_refresh,
)
from app.services.stats.common import (
    _date_str as _date_str,
)
from app.services.stats.common import (
    _day_bounds_utc as _day_bounds_utc,
)
from app.services.stats.common import (
    _utcnow as _utcnow,
)
from app.services.stats.panel import (
    admin_overview,
    user_panel,
)

# 私有实现以 `X as X` 形式显式重导出（PEP 484 约定的 re-export 写法）：api 层与既有测试
# 依赖这三个导入路径，故保持可用；它们不属于本模块的公开 API，因此不列入 `__all__`。
__all__ = [
    "refresh_daily",
    "refresh_for_timestamps",
    "refresh_recent",
    "refresh_user_daily",
    "refresh_user_for_timestamps",
    "startup_refresh",
    "admin_overview",
    "user_panel",
]
