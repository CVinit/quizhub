"""认证业务：注册→验证码、激活、登录。

注册流程（2026-08-22 重构）：图形验证码 → 发送邮箱验证码 → 凭验证码完成注册。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import cast

from fastapi import BackgroundTasks, status
from sqlalchemy import case, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.email import normalize_email
from app.core.errors import DomainError
from app.core.security import create_access_token, gen_verify_code, hash_password, verify_password
from app.models.user import EmailVerification, User
from app.services import mail_service
from app.services.system_service import get_settings

MAX_VERIFY_ATTEMPTS = 5

# 仅用于「登录时邮箱不存在」分支的耗时对齐（见 login）。模块加载时哈希一次，
# 不参与任何真实账号的校验，也不构成可用的凭据。
_DUMMY_PASSWORD_HASH = hash_password("quizhub-timing-equalizer")

logger = logging.getLogger("quizhub")


def _allowed_suffixes(db: Session) -> list[str]:
    raw = get_settings(db, "register").get("register_allowed_email_suffixes", "")
    # 与其他列表型设置（register_allowed_group_ids / upload_allowed_ext）统一兼容中文逗号：
    # 只按英文逗号切分会把 "@a.com，@b.com" 整体当成一个永不匹配的后缀，反而拒掉合法邮箱。
    return [s.strip().lower() for s in raw.replace("，", ",").split(",") if s.strip()]


def _ensure_registration_open(db: Session) -> None:
    if get_settings(db, "register").get("register_open", "true").lower() != "true":
        raise DomainError(status.HTTP_403_FORBIDDEN, "当前未开放注册")


def allowed_register_group_ids(db: Session) -> set[int]:
    """返回允许公开注册自选的分组白名单。

    空配置表示**不允许自选任何分组**（fail-closed，见 test_security_regressions
    的 test_registration_cannot_self_assign_non_public_group）。管理员需在
    系统设置「注册与审批」中用分组下拉显式勾选允许加入的分组。
    """
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
        raise DomainError(
            status.HTTP_400_BAD_REQUEST,
            "该邮箱后缀不允许注册，请使用企业/机构邮箱",
        )


def send_code(db: Session, email: str, bg: BackgroundTasks) -> None:
    """发送注册验证码：校验后缀与是否已注册后，生成验证码并入队邮件。

    防探测：邮箱已注册时**不抛错、不发信**，与未注册走完全相同的成功响应，
    使攻击者无法用状态码枚举注册用户。
    （原实现在此处抛 400，与「统一提示」的注释意图相悖：400/200 的差异本身即 oracle。）

    因此 register 阶段的「该邮箱已注册」提示仅在极少数竞态下才会出现（该邮箱在
    发码之后、注册之前被他人创建，例如管理员同时建号）；正常已注册邮箱根本拿不到
    验证码，register 会停在「验证码错误或已过期」。这是防枚举的必然代价，属预期行为。

    SMTP 未配置时直接 503（fail-closed）：此时验证码永远发不出去，若仍返回成功，
    全新部署会陷入"提示已发送、用户永远收不到、管理员毫无察觉"的死局。
    该判断对所有邮箱一致，不构成注册邮箱枚举 oracle。
    """
    email = normalize_email(email)
    _ensure_registration_open(db)
    check_email_suffix(db, email)
    settings = get_settings(db)
    try:
        mail_service.ensure_configured(settings)
    except mail_service.MailError as exc:
        raise DomainError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "邮件服务尚未配置，暂时无法发送验证码，请联系管理员",
        ) from exc
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        logger.info("[auth] 注册验证码请求命中已注册邮箱，已静默忽略（防枚举）")
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
    # 后台发送：用 send_safely 包一层，SMTP 失败会写日志而不是静默消失
    bg.add_task(mail_service.send_safely, mail_service.send_register_code, settings, email, code)


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
        raise DomainError(status.HTTP_400_BAD_REQUEST, "请至少选择一个分组")
    # 校验分组 id 合法存在，防止伪造
    if gids:
        from app.models.group import Group

        allowed = allowed_register_group_ids(db)
        if not set(gids).issubset(allowed):
            raise DomainError(status.HTTP_403_FORBIDDEN, "所选分组不允许公开注册加入")
        valid = {r[0] for r in db.execute(select(Group.id).where(Group.id.in_(gids))).all()}
        if set(gids) != valid:
            raise DomainError(status.HTTP_400_BAD_REQUEST, "所选分组无效，请重新选择")
    # 表单校验通过后再消费验证码
    if not _consume_code(db, email, code):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "验证码错误或已过期")
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "该邮箱已注册")
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
        raise DomainError(status.HTTP_400_BAD_REQUEST, "验证码不存在或已使用")
    if datetime.fromisoformat(ev.expire_at) < datetime.now(timezone.utc):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "验证码已过期")
    if ev.code != code:
        _record_code_failure(db, ev.id)
        raise DomainError(status.HTTP_400_BAD_REQUEST, "验证码错误")
    # 原子消费：与 _consume_code 同口径。原实现是 ORM 读-判-写（`ev.used = True`），
    # 并发携带同一验证码的两个请求都可能通过校验，使一次性验证码可被重放。
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
    if consumed.rowcount != 1:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "验证码不存在或已使用")
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "验证码无效")
    user.email_verified = True
    if (
        user.status == "pending"
        and get_settings(db, "register").get("new_user_need_approve", "false").lower() != "true"
    ):
        user.status = "active"
    db.commit()


def resend(db: Session, email: str, bg: BackgroundTasks) -> None:
    """旧版：给已注册但未验证用户重发验证码（兼容旧 verify 页面）。

    与 `send_code` 同口径：SMTP 未配置时 fail-closed 抛 503，避免静默丢信。
    """
    email = normalize_email(email)
    settings = get_settings(db)
    try:
        mail_service.ensure_configured(settings)
    except mail_service.MailError as exc:
        raise DomainError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "邮件服务尚未配置，暂时无法发送验证码，请联系管理员",
        ) from exc
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
    bg.add_task(mail_service.send_safely, mail_service.send_register_code, settings, email, code)


def login(db: Session, email: str, password: str) -> dict:
    email = normalize_email(email)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        # 计时侧信道防护：邮箱不存在时也必须付一次 bcrypt 的开销，否则
        # 「命中 ~300ms / 未命中 ~0ms」的差异可被单次请求用来枚举已注册邮箱
        # （与 send_code 的静默防枚举一致）。该 hash 不对应任何真实账号。
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise DomainError(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
    if not verify_password(password, user.password_hash):
        raise DomainError(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
    if not user.email_verified:
        raise DomainError(status.HTTP_403_FORBIDDEN, "请先完成邮箱验证")
    if user.status == "disabled":
        raise DomainError(status.HTTP_403_FORBIDDEN, "账号已被禁用")
    if user.status == "pending":
        raise DomainError(status.HTTP_403_FORBIDDEN, "账号待管理员审批")
    token = create_access_token(user.id, {"role": user.role, "ver": user.token_version})
    return {"access_token": token, "token_type": "bearer", "user": user}


def change_password(db: Session, user: User, old: str, new: str) -> None:
    if not verify_password(old, user.password_hash):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "原密码错误")
    user.password_hash = hash_password(new)
    user.token_version += 1
    db.commit()
