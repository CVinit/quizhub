"""审计日志与草稿路由。"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy.orm import Session

from app.core.deps import dept_scope_ids, get_current_user, require_admin
from app.core.timeutil import business_tz
from app.database import get_db
from app.models.user import User
from app.services import audit_service

router = APIRouter(tags=["audit"])

# 草稿边界：原实现接受任意大小的 JSON 与任意长度的 form_key，任一登录用户
# 可反复写入超大草稿造成 SQLite 体积膨胀与请求期内存峰值。
_MAX_DRAFT_KEY_LEN = 64
_MAX_DRAFT_BYTES = 64 * 1024


# ---------- 审计日志（管理端）----------
@router.get("/admin/audit-logs")
def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: int | None = None,
    action: str | None = None,
    target_type: str | None = None,
    keyword: str | None = None,
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    items, total = audit_service.list_logs(
        db,
        page,
        page_size,
        actor,
        action,
        target_type,
        keyword,
        dept_scope_ids(db, user),
        _iso_bound(from_),
        _iso_bound(to, end_of_day=True),
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def _iso_bound(value: datetime | None, *, end_of_day: bool = False) -> str | None:
    """把查询时间归一为与 created_at 同格式的 UTC ISO 字符串。

    `created_at` 以「UTC ISO + 偏移」存储，按字符串字典序比较。两种入参语义不同：
    - **带偏移**（`2026-02-13T08:00+08:00`）：就是绝对时刻，换算成 UTC 即可；
    - **不带偏移**（纯日期 `2026-02-13`，或无偏移的本地时间）：语义是**业务本地时间**
      （与考试时段解析同一口径），必须按 `BUSINESS_TZ` 解释后再换算成 UTC。

    作为上界时，纯日期要补到当天末尾，否则 `created_at <= 当天 00:00` 会排除当天
    绝大多数记录。判据是「入参是否带偏移」，而不是「换算后是否为 00:00」——后者会把
    显式给出的 UTC 零点（如 `2026-02-13T08:00+08:00`）静默扩成当天末尾。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        local = value.replace(tzinfo=business_tz())
        if end_of_day and value.time() == time(0, 0):
            local = local + timedelta(days=1) - timedelta(microseconds=1)
        return local.astimezone(timezone.utc).isoformat()
    return value.astimezone(timezone.utc).isoformat()


# ---------- 草稿（用户端，长表单自动保存）----------
@router.get("/drafts/{form_key}")
def load_draft(
    form_key: str = Path(..., min_length=1, max_length=_MAX_DRAFT_KEY_LEN),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return audit_service.load_draft(db, user.id, form_key)


@router.put("/drafts/{form_key}")
def save_draft(
    request: Request,
    payload: dict = Body(...),
    form_key: str = Path(..., min_length=1, max_length=_MAX_DRAFT_KEY_LEN),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """保存长表单草稿；对 payload 体积与 form_key 长度设上限。"""
    # 请求体在进入本函数前已被 FastAPI 完整解析：先用 Content-Length 预检，挡掉超大
    # body 的解析期内存峰值（下面的 json 体积校验只能限制落库大小，挡不住解析本身）。
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_DRAFT_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"草稿内容不能超过 {_MAX_DRAFT_BYTES // 1024}KB",
        )
    # 写频率限制：草稿是登录用户可反复写的表，不设限会让 SQLite 体积被持续撑大。
    # 阈值宽松（长表单自动保存通常几秒一次），只用于兜底防刷。
    from app.core.rate_limit import check

    check(f"draft:user:{user.id}", 120, 60, "草稿保存")
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > _MAX_DRAFT_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"草稿内容不能超过 {_MAX_DRAFT_BYTES // 1024}KB",
        )
    return audit_service.save_draft(db, user.id, form_key, payload)


@router.delete("/drafts/{form_key}")
def clear_draft(
    form_key: str = Path(..., min_length=1, max_length=_MAX_DRAFT_KEY_LEN),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return audit_service.clear_draft(db, user.id, form_key)
