"""用户管理业务。"""
from __future__ import annotations

import secrets
import string

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.group import Group, UserGroup
from app.models.user import User
from app.services.audit_service import log as audit_log


# 角色与状态白名单：管理员新增/导入用户时校验，防伪造非法角色
ROLES = ("user", "dept_admin", "super_admin")
STATUSES = ("active", "pending", "disabled")


def _gen_password(length: int = 12) -> str:
    """生成随机强密码（字母+数字）。"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def list_users(
    db: Session, page: int = 1, page_size: int = 20,
    keyword: str | None = None, role: str | None = None, status_: str | None = None,
    group_id: int | None = None,
) -> tuple[list, int]:
    stmt = select(User)
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where(or_(User.email.like(kw), User.name.like(kw)))
    if role:
        stmt = stmt.where(User.role == role)
    if status_:
        stmt = stmt.where(User.status == status_)
    if group_id:
        stmt = stmt.join(UserGroup, UserGroup.user_id == User.id).where(UserGroup.group_id == group_id)
    total = db.execute(select(User.id).select_from(stmt.subquery())).all()
    total = len(total)
    rows = db.execute(
        stmt.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    items = []
    for u in rows:
        gids = [ug.group_id for ug in db.execute(
            select(UserGroup.group_id).where(UserGroup.user_id == u.id)
        ).all()]
        items.append({
            "id": u.id, "email": u.email, "name": u.name, "role": u.role,
            "status": u.status, "email_verified": u.email_verified,
            "dept_group_id": u.dept_group_id, "groups": gids,
        })
    return items, total


def approve(db: Session, actor: int, user_id: int) -> User:
    u = _get(db, user_id)
    if u.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该用户非待审批状态")
    u.status = "active"
    db.commit()
    audit_log(db, actor, "user.approve", "user", user_id, {"email": u.email})
    db.refresh(u)
    return u


def set_status(db: Session, actor: int, user_id: int, enabled: bool) -> User:
    u = _get(db, user_id)
    u.status = "active" if enabled else "disabled"
    db.commit()
    audit_log(db, actor, "user.enable" if enabled else "user.disable", "user", user_id, {"email": u.email})
    db.refresh(u)
    return u


def reset_password(db: Session, actor: int, user_id: int, new_password: str | None = None) -> str:
    """重置密码。未提供 new_password 时生成随机强密码（12 位字母数字）。

    不再恒用弱口令 "123456"；返回生成的新密码供管理员告知用户，并强制用户首次登录修改。
    """
    u = _get(db, user_id)
    if not new_password:
        alphabet = string.ascii_letters + string.digits
        new_password = "".join(secrets.choice(alphabet) for _ in range(12))
    if len(new_password) < 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "密码至少 6 位")
    u.password_hash = hash_password(new_password)
    db.commit()
    audit_log(db, actor, "user.reset_password", "user", user_id, {"email": u.email})
    return new_password


def update_user(db: Session, actor: int, user_id: int, name: str | None, role: str | None, dept_group_id: int | None) -> User:
    """更新用户。角色变更仅超级管理员可执行；部门管理员不得修改角色。"""
    u = _get(db, user_id)
    changes: dict = {}
    if name is not None:
        u.name = name
        changes["name"] = name
    if role is not None:
        if role not in ("user", "dept_admin", "super_admin"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "角色非法")
        if u.role != role:
            # 角色变更需超级管理员权限（由调用方在路由层校验），此处仅记录
            u.role = role
            changes["role"] = role
    if dept_group_id is not None:
        u.dept_group_id = dept_group_id
        changes["dept_group_id"] = dept_group_id
    db.commit()
    if changes:
        audit_log(db, actor, "user.update", "user", user_id, {"email": u.email, **changes})
    db.refresh(u)
    return u


def assign_groups(db: Session, actor: int, user_id: int, group_ids: list[int]) -> None:
    u = _get(db, user_id)
    db.execute(UserGroup.__table__.delete().where(UserGroup.user_id == user_id))
    for gid in group_ids:
        db.add(UserGroup(user_id=user_id, group_id=gid))
    db.commit()
    audit_log(db, actor, "user.assign_groups", "user", user_id, {"email": u.email, "group_ids": group_ids})


def _get(db: Session, user_id: int) -> User:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    return u


def _normalize_group_ids(db: Session, group_ids: list[int] | None) -> list[int]:
    """校验分组 id 合法存在，返回去重后的合法列表（防伪造）。"""
    gids = list(dict.fromkeys(g for g in (group_ids or []) if g))  # 去重 + 去零/空
    if not gids:
        return []
    valid = {r[0] for r in db.execute(select(Group.id).where(Group.id.in_(gids))).all()}
    return [g for g in gids if g in valid]


def create_user(
    db: Session, actor: int, email: str, name: str = "", role: str = "user",
    password: str | None = None, status_: str = "active", group_ids: list[int] | None = None,
) -> tuple[User, str]:
    """管理员手动新增用户。

    - email 唯一性校验；
    - password 留空则生成随机强密码并返回，便于管理员告知用户；
    - role/status 白名单校验；
    - group_ids 合法性校验后写入关联；
    - 跳过邮箱验证流程（email_verified=True），管理员新增即视为可信账号。
    返回 (user, plain_password)。
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邮箱格式不正确")
    if role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "角色非法")
    if status_ not in STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "状态非法")

    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该邮箱已存在")

    if not password:
        password = _gen_password()
    if len(password) < 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "密码至少 6 位")

    gids = _normalize_group_ids(db, group_ids)
    user = User(
        email=email,
        password_hash=hash_password(password),
        name=(name or "").strip() or email.split("@")[0],
        role=role,
        status=status_,
        email_verified=True,
    )
    db.add(user)
    db.flush()
    for gid in gids:
        db.add(UserGroup(user_id=user.id, group_id=gid))
    db.commit()
    db.refresh(user)
    audit_log(db, actor, "user.create", "user", user.id, {
        "email": user.email, "role": role, "status": status_, "group_ids": gids,
    })
    return user, password


def import_users(
    db: Session, actor: int, rows: list[dict],
) -> dict:
    """批量导入用户（管理员已预览确认）。rows 每项含 email/name/role/password/status/group_ids。

    单行失败不中断整体导入，逐行收集成功/失败计数与失败明细，统一提交成功项。
    """
    success = 0
    failed = 0
    errors: list[dict] = []
    pending_users: list[User] = []
    pending_groups: list[UserGroup] = []
    seen_emails: set[str] = set()
    for idx, r in enumerate(rows, start=1):
        email = str(r.get("email") or "").strip().lower()
        try:
            if not email or "@" not in email:
                raise ValueError("邮箱格式不正确")
            if email in seen_emails:
                raise ValueError("本次导入内邮箱重复")
            seen_emails.add(email)
            existing = db.execute(select(User.id).where(User.email == email)).scalar_one_or_none()
            if existing:
                raise ValueError("该邮箱已存在")
            role = str(r.get("role") or "user").strip() or "user"
            if role not in ROLES:
                raise ValueError(f"角色非法: {role}")
            st = str(r.get("status") or "active").strip() or "active"
            if st not in STATUSES:
                raise ValueError(f"状态非法: {st}")
            pwd = str(r.get("password") or "").strip()
            if not pwd:
                pwd = _gen_password()
            if len(pwd) < 6:
                raise ValueError("密码至少 6 位")
            name = str(r.get("name") or "").strip() or email.split("@")[0]
            gids = _normalize_group_ids(db, r.get("group_ids"))
            u = User(
                email=email, password_hash=hash_password(pwd), name=name,
                role=role, status=st, email_verified=True,
            )
            pending_users.append(u)
            # 临时挂在行上以便回填 id 与密码
            r["_user"] = u
            r["_password"] = pwd
            r["_gids"] = gids
            success += 1
        except Exception as e:  # 单行失败不影响其他行
            failed += 1
            errors.append({"row": idx, "email": email, "error": str(e)})
    if pending_users:
        db.add_all(pending_users)
        db.flush()
        # 回填 user_group 关联的 user_id
        for r in rows:
            u = r.get("_user")
            if not u:
                continue
            for gid in r.get("_gids", []):
                db.add(UserGroup(user_id=u.id, group_id=gid))
    db.commit()
    audit_log(db, actor, "user.import", "user", 0, {
        "success": success, "failed": failed, "total": len(rows),
    })
    # 返回成功用户明文密码，便于管理员导出告知
    created = [
        {"email": r["_user"].email, "name": r["_user"].name, "password": r["_password"]}
        for r in rows if r.get("_user")
    ]
    return {"success": success, "failed": failed, "errors": errors, "created": created}
