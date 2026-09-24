"""用户管理路由（管理端）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import dept_scope_ids, require_admin
from app.core.uploads import read_limited
from app.database import get_db
from app.models.user import ROLE_SUPER_ADMIN, ROLE_USER, User
from app.schemas.group import UserGroupAssign
from app.schemas.user import ResetPasswordIn, UserCreateIn, UserListOut, UserUpdate
from app.services import user_import_service, user_service
from app.utils import user_excel
from app.utils.excel import XLSX_MEDIA_TYPE

router = APIRouter(prefix="/admin/users", tags=["users"])


@router.get("", response_model=UserListOut)
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
    if payload.role != ROLE_USER and user.role != ROLE_SUPER_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅超级管理员可创建管理员账号")
    scope = dept_scope_ids(db, user)
    # 部门管理员创建的用户必须归属其范围内分组，且分配的分组也在范围内
    if scope is not None:
        for gid in payload.group_ids:
            if gid not in scope:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "无权分配该分组")
    u = user_service.create_user(
        db,
        user.id,
        payload.email,
        payload.name,
        payload.role,
        payload.password,
        payload.status,
        payload.group_ids,
        # 部门管理员新增用户时自动归属其部门；与创建同一事务写入，避免二次 commit
        dept_group_id=user.dept_group_id if scope is not None else None,
        actor_role=user.role,
    )
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
    if payload.role is not None and user.role != ROLE_SUPER_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅超级管理员可修改用户角色")
    scope = dept_scope_ids(db, user)
    # 显式传 null 表示「清空部门归属」；未传字段则保持不变（PATCH 语义）
    clear_dept_group = "dept_group_id" in payload.model_fields_set and payload.dept_group_id is None
    u = user_service.update_user(
        db,
        user.id,
        user_id,
        payload.name,
        payload.role,
        payload.dept_group_id,
        scope,
        clear_dept_group=clear_dept_group,
        actor_role=user.role,
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
# 模板文件名（RFC 5987 百分号编码的中文名）抽成常量：避免超长行，也便于统一维护。
USER_TEMPLATE_FILENAME = "%E7%94%A8%E6%88%B7%E5%AF%BC%E5%85%A5%E6%A8%A1%E6%9D%BF.xlsx"  # 用户导入模板.xlsx


@router.get("/import/template")
def download_user_template(_user: User = Depends(require_admin)):
    buf = user_excel.build_template()
    return StreamingResponse(
        buf,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{USER_TEMPLATE_FILENAME}"},
    )


@router.post("/import/preview")
async def import_preview(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    from app.core.rate_limit import check

    check(f"user-import-preview:user:{user.id}", 20, 3600, "用户导入")
    max_mb = __import_settings_max_mb(db)
    # 用户模板固定为 .xlsx（openpyxl 只解析 xlsx），不跟随题库导入的扩展名设置；
    # 扩展名 + 体积上限 + 分块读取统一走 core.uploads（与题库导入共用同一实现）
    content = await read_limited(
        file,
        max_bytes=int(max_mb * 1024 * 1024),
        allowed_ext={".xlsx"},
        ext_message="仅支持 .xlsx 文件",
    )
    try:
        # 解析 xlsx 并对每行做 bcrypt（单次约 300ms，行数上限 5000）是纯 CPU 工作：
        # 直接在 async 路由内调用会独占事件循环数分钟，阻塞所有并发请求。
        # 丢进线程池执行，保持事件循环可用。
        return await run_in_threadpool(user_excel.preview, content, user.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/import")
def import_users(
    confirm_token: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    # 与手动新增一致：非超级管理员不得导入管理员账号（垂直越权防护）。
    # 先 peek（不消费）做角色校验，通过后再 consume：校验不通过不作废本次预览。
    rows = user_excel.peek_preview(confirm_token, user.id)
    if user.role != ROLE_SUPER_ADMIN:
        for r in rows:
            if str(r.get("role") or ROLE_USER) != ROLE_USER:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "仅超级管理员可创建管理员账号")
    rows = user_excel.consume_preview(confirm_token, user.id)
    scope = dept_scope_ids(db, user)
    res = user_import_service.import_users(
        db,
        user.id,
        rows,
        scope=scope,
        actor_role=user.role,
        # 部门管理员导入的用户自动归属其部门：与手动新增（create_user）同口径。
        # 模板的「分组ID」列允许留空，不自动归属会产生「自己建的号自己看不到、管不了」。
        dept_group_id=user.dept_group_id if scope is not None else None,
    )
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
