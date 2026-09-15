"""认证业务：注册→验证码、激活、登录。

注册流程（2026-08-22 重构）：图形验证码 → 发送邮箱验证码 → 凭验证码完成注册。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import case, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.email import normalize_email
from app.core.security import create_access_token, gen_verify_code, hash_password, verify_password
from app.models.user import EmailVerification, User
from app.services import mail_service
from app.services.system_service import get_settings

MAX_VERIFY_ATTEMPTS = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _allowed_suffixes(db: Session) -> list[str]:
    raw = get_settings(db, "register").get("register_allowed_email_suffixes", "")
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def _ensure_registration_open(db: Session) -> None:
    if get_settings(db, "register").get("register_open", "true").lower() != "true":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "当前未开放注册")


def allowed_register_group_ids(db: Session) -> set[int]:
    """返回允许公开注册自选的分组；空配置表示不允许自选分组。"""
    raw = get_settings(db, "register").get("register_allowed_group_ids", "")
    ids: set[int] = set()
    for value in raw.replace("，", ",").split(","):
        try:
            if value.strip():
                ids.add(int(value.strip()))
        except ValueError:
            continue
    return ids


def check_email_suffix(db: Session, email: str) -> None:
    """校验邮箱后缀是否在允许名单内（留空表示不限制）。"""
    suffixes = _allowed_suffixes(db)
    if not suffixes:
        return
    low = normalize_email(email)
    if not any(low.endswith(suf) for suf in suffixes):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "该邮箱后缀不允许注册，请使用企业/机构邮箱",
        )


def send_code(db: Session, email: str, bg: BackgroundTasks) -> None:
    """发送注册验证码：先校验后缀与未注册，再生成验证码并入队邮件。"""
    email = normalize_email(email)
    _ensure_registration_open(db)
    check_email_suffix(db, email)
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        # 防探测：不暴露"邮箱已注册"，统一提示需输入验证码
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请输入发送到该邮箱的验证码完成注册")
    code = gen_verify_code()
    db.add(
        EmailVerification(
            email=email,
            code=code,
            expire_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            used=False,
        )
    )
    db.commit()
    bg.add_task(mail_service.send_register_code, get_settings(db), email, code)


def register(
    db: Session,
    email: str,
    password: str,
    name: str,
    code: str,
    bg: BackgroundTasks,
    group_ids: list[int] | None = None,
) -> User:
    """凭邮箱验证码完成注册（自验证：注册即 email_verified=True）。

    group_ids：注册时选择的分组；若设置开启 register_group_required 则至少需要一个。
    分组校验在消费验证码之前完成，避免表单错误烧掉验证码。
    """
    email = normalize_email(email)
    _ensure_registration_open(db)
    check_email_suffix(db, email)
    settings = get_settings(db, "register")
    need_approve = settings.get("new_user_need_approve", "false").lower() == "true"
    group_required = settings.get("register_group_required", "false").lower() == "true"
    gids = list(dict.fromkeys(group_ids or []))
    if group_required and not gids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请至少选择一个分组")
    # 校验分组 id 合法存在，防止伪造
    if gids:
        from app.models.group import Group

        allowed = allowed_register_group_ids(db)
        if not set(gids).issubset(allowed):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "所选分组不允许公开注册加入")
        valid = {r[0] for r in db.execute(select(Group.id).where(Group.id.in_(gids))).all()}
        if set(gids) != valid:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "所选分组无效，请重新选择")
    # 表单校验通过后再消费验证码
    if not _consume_code(db, email, code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码错误或已过期")
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该邮箱已注册")
    user = User(
        email=email,
        password_hash=hash_password(password),
        name=name or email.split("@")[0],
        role="user",
        status="pending" if need_approve else "active",
        email_verified=True,
    )
    db.add(user)
    db.flush()  # 拿到 user.id 再写关联
    if gids:
        from app.models.group import UserGroup

        for gid in gids:
            db.add(UserGroup(user_id=user.id, group_id=gid))
    db.commit()
    db.refresh(user)
    return user


def _is_code_valid(db: Session, email: str, code: str) -> bool:
    ev = _latest_unused(db, email)
    if not ev:
        return False
    if datetime.fromisoformat(ev.expire_at) < datetime.now(timezone.utc):
        return False
    return ev.code == code


def _consume_code(db: Session, email: str, code: str) -> bool:
    ev = _latest_unused(db, email)
    if not ev or datetime.fromisoformat(ev.expire_at) < datetime.now(timezone.utc):
        return False
    if ev.code != code:
        _record_code_failure(db, ev.id)
        return False
    consumed = cast(
        CursorResult,
        db.execute(
            update(EmailVerification)
            .where(
                EmailVerification.id == ev.id,
                EmailVerification.used == False,  # noqa: E712
                EmailVerification.code == code,
                EmailVerification.attempts < MAX_VERIFY_ATTEMPTS,
            )
            .values(used=True)
        ),
    )
    db.commit()
    return consumed.rowcount == 1


def _record_code_failure(db: Session, verification_id: int) -> None:
    db.execute(
        update(EmailVerification)
        .where(
            EmailVerification.id == verification_id,
            EmailVerification.used == False,  # noqa: E712
            EmailVerification.attempts < MAX_VERIFY_ATTEMPTS,
        )
        .values(
            attempts=EmailVerification.attempts + 1,
            used=case((EmailVerification.attempts + 1 >= MAX_VERIFY_ATTEMPTS, True), else_=False),
        )
    )
    db.commit()


def _latest_unused(db: Session, email: str) -> EmailVerification | None:
    return (
        db.execute(
            select(EmailVerification)
            .where(
                EmailVerification.email == email,
                EmailVerification.used == False,  # noqa: E712
                EmailVerification.attempts < MAX_VERIFY_ATTEMPTS,
            )
            .order_by(EmailVerification.id.desc())
        )
        .scalars()
        .first()
    )


# ---------- 旧版邮箱激活流程（保留向后兼容，前端已切换到新流程）----------
def verify_email(db: Session, email: str, code: str) -> None:
    email = normalize_email(email)
    ev = _latest_unused(db, email)
    if not ev:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码不存在或已使用")
    if datetime.fromisoformat(ev.expire_at) < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码已过期")
    if ev.code != code:
        _record_code_failure(db, ev.id)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码错误")
    ev.used = True
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码无效")
    user.email_verified = True
    if (
        user.status == "pending"
        and get_settings(db, "register").get("new_user_need_approve", "false").lower() != "true"
    ):
        user.status = "active"
    db.commit()


def resend(db: Session, email: str, bg: BackgroundTasks) -> None:
    """旧版：给已注册但未验证用户重发验证码（兼容旧 verify 页面）。"""
    email = normalize_email(email)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user:
        return
    if user.email_verified:
        return
    code = gen_verify_code()
    db.add(
        EmailVerification(
            email=email,
            code=code,
            expire_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            used=False,
        )
    )
    db.commit()
    bg.add_task(mail_service.send_register_code, get_settings(db), email, code)


def login(db: Session, email: str, password: str) -> dict:
    email = normalize_email(email)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
    if not user.email_verified:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "请先完成邮箱验证")
    if user.status == "disabled":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被禁用")
    if user.status == "pending":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号待管理员审批")
    token = create_access_token(user.id, {"role": user.role, "ver": user.token_version})
    return {"access_token": token, "token_type": "bearer", "user": user}


def change_password(db: Session, user: User, old: str, new: str) -> None:
    if not verify_password(old, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "原密码错误")
    user.password_hash = hash_password(new)
    user.token_version += 1
    db.commit()
