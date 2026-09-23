"""面板路由：用户端个人面板 + 管理端概览。

注：排行榜（`GET /rank`）与 `services/stats/rank.py` 已于 2026-09-23 按产品决策整体下线，
`rank_visible` 设置项一并移除（存量设置行由 `scripts/migrate_2026_09_23.py` 清理）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import dept_scope_ids, get_current_user, require_admin, require_super
from app.database import get_db
from app.models.user import User
from app.services import stats_service

router = APIRouter(tags=["panel"])


# ---------- 用户端面板 ----------
@router.get("/panel/me")
def user_panel(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return stats_service.user_panel(db, user)


# ---------- 管理端概览（部门管理员按数据范围过滤，超管全量）----------
@router.get("/admin/panel/overview")
def admin_overview(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return stats_service.admin_overview(db, dept_scope_ids(db, user))


@router.post("/admin/panel/refresh")
def refresh_stats(
    days: int = Query(default=1, ge=1, le=60), db: Session = Depends(get_db), _user: User = Depends(require_super)
):
    n = stats_service.refresh_recent(db, days)
    return {"refreshed": n}
