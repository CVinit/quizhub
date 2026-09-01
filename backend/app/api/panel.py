"""面板与排行路由。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import dept_scope_ids, get_current_user, require_admin, require_super
from app.database import get_db
from app.models.user import User
from app.services import stats_service, system_service

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


# ---------- 排行 ----------
@router.get("/rank")
def rank(
    dimension: Literal["accuracy", "count", "score", "streak"] = Query("accuracy"),
    scope: Literal["self", "group"] = Query("self"),
    range: Literal["7d", "30d", "all"] = Query("7d"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 排行可见性开关：关闭时对普通用户隐藏（管理员仍可在后台查看）
    if system_service.get_settings(db, "general").get("rank_visible", "true") != "true" and user.role == "user":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "排行榜已被关闭")
    return stats_service.rank(db, dimension, scope, range, user.id)
