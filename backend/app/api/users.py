"""用户管理路由（管理端）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy.orm import Session

from app.core.deps import dept_scope_ids, require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.group import UserGroupAssign
from app.schemas.user import UserUpdate
from app.services import user_service
from app.utils import user_excel

router = APIRouter(prefix="/admin/users", tags=["users"])


class ResetPasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(min_length=6, max_length=72)

    @field_validator("new_password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码 UTF-8 编码后不能超过 72 字节")
        return value


class UserCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    name: str = Field(default="", max_length=50)
    role: str = Field(default="user")
    password: str = Field(min_length=6, max_length=72)
    status: str = Field(default="active")
    group_ids: list[int] = Field(default_factory=list)

    @field_validator("password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码 UTF-8 编码后不能超过 72 字节")
        return value


@router.get("")
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    role: str | None = None,
    status: str | None = None,
    group_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    scope = dept_scope_ids(db, user)
    items, total = user_service.list_users(
        db,
        page,
        page_size,
        keyword,
        role,
        status,
        group_id,
        scope,
    )
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """手动新增用户。角色变更类（dept_admin/super_admin）仅超级管理员可创建。

    部门管理员只能把用户分配到其部门子树内的分组。
    """
    if payload.role != "user" and user.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅超级管理员可创建管理员账号")
    scope = dept_scope_ids(db, user)
    # 部门管理员创建的用户必须归属其范围内分组，且分配的分组也在范围内
    if scope is not None:
        for gid in payload.group_ids:
            if gid not in scope:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "无权分配该分组")
    u, _ = user_service.create_user(
        db,
        user.id,
        payload.email,
        payload.name,
        payload.role,
        payload.password,
        payload.status,
        payload.group_ids,
    )
    # 部门管理员新增用户时，自动归属其部门
    if scope is not None and u.dept_group_id is None:
        u.dept_group_id = user.dept_group_id
        db.commit()
        db.refresh(u)
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "role": u.role,
        "status": u.status,
        "email_verified": u.email_verified,
        "dept_group_id": u.dept_group_id,
    }


@router.put("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    # 角色变更仅超级管理员可执行（部门管理员不得提权）
    if payload.role is not None and user.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅超级管理员可修改用户角色")
    scope = dept_scope_ids(db, user)
    u = user_service.update_user(
        db,
        user.id,
        user_id,
        payload.name,
        payload.role,
        payload.dept_group_id,
        scope,
    )
    return _to_dict(u)


@router.post("/{user_id}/approve")
def approve(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _to_dict(user_service.approve(db, user.id, user_id, dept_scope_ids(db, user)))


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """删除用户（仅超级管理员；不能删自己/最后一个超管）。级联清理其练习与考试数据。"""
    user_service.delete_user(db, user.id, user.role, user_id, dept_scope_ids(db, user))
    return None


@router.post("/{user_id}/disable")
def disable(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _to_dict(user_service.set_status(db, user.id, user_id, False, dept_scope_ids(db, user)))


@router.post("/{user_id}/enable")
def enable(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _to_dict(user_service.set_status(db, user.id, user_id, True, dept_scope_ids(db, user)))


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    payload: ResetPasswordIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    user_service.reset_password(db, user.id, user_id, payload.new_password, dept_scope_ids(db, user))
    return {"success": True}


@router.post("/{user_id}/groups")
def assign_groups(
    user_id: int,
    payload: UserGroupAssign,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    user_service.assign_groups(db, user.id, user_id, payload.group_ids, dept_scope_ids(db, user))
    return {"success": True}


# ---------- 批量导入用户 ----------
@router.get("/import/template")
def download_user_template(_user: User = Depends(require_admin)):
    buf = user_excel.build_template()
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''%E7%94%A8%E6%88%B7%E5%AF%BC%E5%85%A5%E6%A8%A1%E6%9D%BF.xlsx"
        },
    )


@router.post("/import/preview")
async def import_preview(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    from app.core.rate_limit import check

    check(f"user-import-preview:user:{user.id}", 20, 3600, "用户导入")
    settings = __import_settings_max_mb(db)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅支持 .xlsx 文件")
    max_bytes = int(settings * 1024 * 1024)
    declared = file.size or 0
    if declared and declared > max_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"文件超过上限 {settings}MB")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"文件超过上限 {settings}MB")
        chunks.append(chunk)
    content = b"".join(chunks)
    try:
        return user_excel.preview(db, content, user.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/import")
def import_users(
    confirm_token: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    # 与手动新增一致：非超级管理员不得导入管理员账号（垂直越权防护）
    if user.role != "super_admin":
        rows = user_excel.consume_preview(confirm_token, user.id)
        for r in rows:
            if str(r.get("role") or "user") != "user":
                raise HTTPException(status.HTTP_403_FORBIDDEN, "仅超级管理员可创建管理员账号")
    else:
        rows = user_excel.consume_preview(confirm_token, user.id)
    res = user_service.import_users(db, user.id, rows, scope=dept_scope_ids(db, user), actor_role=user.role)
    return res


def __import_settings_max_mb(db: Session) -> float:
    from app.services.system_service import get_settings

    return float(get_settings(db, "upload").get("upload_max_size_mb", "10") or "10")


def _to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "role": u.role,
        "status": u.status,
        "email_verified": u.email_verified,
        "dept_group_id": u.dept_group_id,
    }
