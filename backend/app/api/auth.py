"""认证路由。

注册流程：图形验证码 → 发送邮箱验证码 → 凭验证码完成注册。
公网部署防扫描：双维度限流（客户端 IP + 邮箱/账号），见 app/core/rate_limit.py。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.orm import Session

from app.core.captcha import store as captcha_store
from app.core.deps import get_current_user
from app.core.rate_limit import check, ip_limit
from app.database import get_db
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordIn,
    LoginIn,
    RegisterIn,
    ResendIn,
    SendCodeIn,
    TokenOut,
    UserOut,
    VerifyIn,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

# 限流阈值：
# - captcha：IP 60/分钟（前端按需刷新，足够；防批量预刷）
# - send-code：IP 10/小时、邮箱 3/小时（防验证码邮件轰炸）
# - register：邮箱 3/小时（按 email 维度，captcha 校验已在 send-code 完成）
# - verify：邮箱 5 次/10 分钟（旧版激活流程，兼容保留）
# - login：IP 10/分钟、账号 8/5 分钟
# - resend：邮箱 3/10 分钟、IP 10/10 分钟
# - change-password：用户 5/5 分钟


@router.get("/captcha")
def captcha(_ip: None = Depends(ip_limit("captcha", 60, 60))):
    """获取图形验证码：返回 captcha_id 与 SVG data_uri。"""
    cid, data_uri = captcha_store.generate()
    return {"captcha_id": cid, "image": data_uri}


@router.post("/send-code")
def send_code(
    payload: SendCodeIn,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    _ip: None = Depends(ip_limit("send-code", 10, 3600)),
):
    """发送注册验证码：必须先通过图形验证码校验。"""
    if not captcha_store.verify(payload.captcha_id, payload.captcha_code):
        raise _bad("图形验证码错误或已过期")
    check(f"send-code:email:{payload.email.lower()}", 3, 3600, "发送验证码")
    auth_service.send_code(db, payload.email, bg)
    return {"success": True, "message": "验证码已发送，请查收邮箱"}


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterIn,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    _ip: None = Depends(ip_limit("register", 10, 3600)),
):
    check(f"register:email:{payload.email.lower()}", 3, 3600, "注册")
    return auth_service.register(
        db,
        payload.email,
        payload.password,
        payload.name,
        payload.code,
        bg,
        payload.group_ids,
    )


@router.get("/register-groups")
def register_groups(db: Session = Depends(get_db)):
    """公开接口：返回可选分组树与是否必选，供注册页选择分组（无需登录）。"""
    from app.services.group_service import build_tree
    from app.services.system_service import get_settings

    settings = get_settings(db, "register")
    required = settings.get("register_group_required", "false").lower() == "true"
    return {"groups": build_tree(db), "required": required}


@router.post("/verify")
def verify(
    payload: VerifyIn,
    db: Session = Depends(get_db),
    _ip: None = Depends(ip_limit("verify", 30, 600)),
):
    # 旧版激活流程（兼容）：验证码爆破防护，单邮箱 10 分钟内最多 5 次
    check(f"verify:email:{payload.email.lower()}", 5, 600, "验证")
    auth_service.verify_email(db, payload.email, payload.code)
    return {"success": True}


@router.post("/login", response_model=TokenOut)
def login(
    payload: LoginIn,
    db: Session = Depends(get_db),
    _ip: None = Depends(ip_limit("login", 10, 60)),
):
    check(f"login:account:{payload.username.lower()}", 8, 300, "登录")
    return auth_service.login(db, payload.username, payload.password)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/resend-verification")
def resend(
    payload: ResendIn,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    _ip: None = Depends(ip_limit("resend", 10, 600)),
):
    check(f"resend:email:{payload.email.lower()}", 3, 600, "重发验证码")
    auth_service.resend(db, payload.email, bg)
    return {"success": True}


@router.post("/change-password")
def change_password(
    payload: ChangePasswordIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check(f"change-pwd:user:{user.id}", 5, 300, "修改密码")
    auth_service.change_password(db, user, payload.old_password, payload.new_password)
    return {"success": True}


def _bad(msg: str):
    from fastapi import HTTPException

    return HTTPException(status.HTTP_400_BAD_REQUEST, msg)
