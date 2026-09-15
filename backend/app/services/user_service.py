"""用户管理业务。"""

from __future__ import annotations

from collections import defaultdict
from typing import cast

from fastapi import HTTPException, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.email import normalize_email
from app.core.security import hash_password
from app.models.group import Group, UserGroup
from app.models.user import USER_ROLE, USER_STATUS, User
from app.services.audit_service import log as audit_log

# 角色与状态白名单：管理员新增/导入用户时校验，防伪造非法角色。
# 直接复用模型层常量，避免同一枚举在多处漂移（历史上曾有三份副本）。
ROLES = USER_ROLE
STATUSES = USER_STATUS


def list_users(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    role: str | None = None,
    status_: str | None = None,
    group_id: int | None = None,
    scope: set[int] | None = None,
) -> tuple[list, int]:
    """用户列表。

    scope 为部门管理员数据范围（分组 id 集合）：非空时仅返回 dept_group_id
    或任一 user_groups 落在该范围内的用户；为 None（super_admin）时不过滤。
    """
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
    if scope is not None:
        # 部门管理员范围：dept_group_id 在子树内，或经 user_groups 关联到子树内
        in_scope_ids = {r[0] for r in db.execute(select(UserGroup.user_id).where(UserGroup.group_id.in_(scope))).all()}
        stmt = stmt.where(
            or_(
                User.dept_group_id.in_(scope),
                User.id.in_(in_scope_ids) if in_scope_ids else User.id < 0,
            )
        )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
    rows = db.execute(stmt.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    group_map: dict[int, list[int]] = defaultdict(list)
    if rows:
        for uid, gid in db.execute(
            select(UserGroup.user_id, UserGroup.group_id).where(UserGroup.user_id.in_([u.id for u in rows]))
        ).all():
            group_map[uid].append(gid)
    items = []
    for u in rows:
        items.append(
            {
                "id": u.id,
                "email": u.email,
                "name": u.name,
                "role": u.role,
                "status": u.status,
                "email_verified": u.email_verified,
                "dept_group_id": u.dept_group_id,
                "groups": group_map.get(u.id, []),
            }
        )
    return items, total


def _check_scope(db: Session, user_id: int, scope: set[int] | None) -> None:
    """部门管理员操作前校验目标用户落在其数据范围内，否则 403。"""
    from app.core.deps import user_in_scope

    if not user_in_scope(db, user_id, scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权操作该用户")


def approve(db: Session, actor: int, user_id: int, scope: set[int] | None = None) -> User:
    _check_scope(db, user_id, scope)
    u = _get(db, user_id)
    if u.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该用户非待审批状态")
    u.status = "active"
    db.commit()
    audit_log(db, actor, "user.approve", "user", user_id)
    db.refresh(u)
    return u


def set_status(db: Session, actor: int, user_id: int, enabled: bool, scope: set[int] | None = None) -> User:
    _check_scope(db, user_id, scope)
    u = _get(db, user_id)
    u.status = "active" if enabled else "disabled"
    db.commit()
    audit_log(db, actor, "user.enable" if enabled else "user.disable", "user", user_id)
    db.refresh(u)
    return u


def delete_user(db: Session, actor: int, actor_role: str, user_id: int, scope: set[int] | None = None) -> None:
    """删除用户（仅超级管理员，且不能删自己/最后一个超级管理员）。

    级联清理该用户的练习/考试/复核/分组数据；审计日志与考试定义、试卷模板
    保留（仅把 creator 引用置空），保持历史可追溯。
    """
    from app.models.exam import ExamDefinition, PaperTemplate
    from app.models.record import ExamResult, ExamSession, PracticeRecord, QuestionState, ShortAnswerReview
    from app.models.system import AuditLog, Draft
    from app.models.user import EmailVerification

    if actor_role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅超级管理员可删除用户")
    if actor == user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能删除当前登录账号")
    _check_scope(db, user_id, scope)
    u = _get(db, user_id)
    if u.role == "super_admin":
        remaining = db.execute(
            select(func.count()).select_from(User).where(User.role == "super_admin", User.id != user_id)
        ).scalar_one()
        if remaining == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能删除最后一个超级管理员账号")

    # 本人考试会话 → 成绩 → 简答复核，按外键依赖顺序清理
    session_ids = [r[0] for r in db.execute(select(ExamSession.id).where(ExamSession.user_id == user_id)).all()]
    if session_ids:
        result_ids = (
            select(ExamResult.id).where(ExamResult.exam_session_id.in_(session_ids)).scalar_subquery()
        )
        db.execute(delete(ShortAnswerReview).where(ShortAnswerReview.exam_result_id.in_(result_ids)))
        db.execute(delete(ExamResult).where(ExamResult.exam_session_id.in_(session_ids)))
        db.execute(delete(ExamSession).where(ExamSession.id.in_(session_ids)))
    # 兜底清理无会话关联的遗留成绩/复核（历史数据），以及练习记录、掌握度
    db.execute(delete(ExamResult).where(ExamResult.user_id == user_id))
    db.execute(delete(ShortAnswerReview).where(ShortAnswerReview.user_id == user_id))
    db.execute(delete(PracticeRecord).where(PracticeRecord.user_id == user_id))
    db.execute(delete(QuestionState).where(QuestionState.user_id == user_id))
    db.execute(delete(UserGroup).where(UserGroup.user_id == user_id))
    db.execute(delete(Draft).where(Draft.user_id == user_id))
    db.execute(delete(EmailVerification).where(EmailVerification.email == u.email))
    # 保留审计日志与考试/模板实体，仅解除对被删用户的引用
    db.execute(update(AuditLog).where(AuditLog.actor == user_id).values(actor=None))
    db.execute(update(ShortAnswerReview).where(ShortAnswerReview.reviewer == user_id).values(reviewer=None))
    db.execute(update(ExamDefinition).where(ExamDefinition.created_by == user_id).values(created_by=None))
    db.execute(update(PaperTemplate).where(PaperTemplate.created_by == user_id).values(created_by=None))
    db.delete(u)
    db.commit()
    audit_log(db, actor, "user.delete", "user", user_id, {"email": u.email})


def reset_password(
    db: Session,
    actor: int,
    user_id: int,
    new_password: str,
    scope: set[int] | None = None,
) -> str:
    """重置密码。新密码由管理员通过安全渠道提供，不在响应中返回。"""
    _check_scope(db, user_id, scope)
    u = _get(db, user_id)
    if len(new_password) < 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "密码至少 6 位")
    u.password_hash = hash_password(new_password)
    u.token_version += 1
    db.commit()
    audit_log(db, actor, "user.reset_password", "user", user_id)
    return new_password


def update_user(
    db: Session,
    actor: int,
    user_id: int,
    name: str | None,
    role: str | None,
    dept_group_id: int | None,
    scope: set[int] | None = None,
) -> User:
    """更新用户。角色变更仅超级管理员可执行；部门管理员不得修改角色。"""
    _check_scope(db, user_id, scope)
    u = _get(db, user_id)
    changes: dict = {}
    if name is not None:
        u.name = name
        changes["name"] = name
    if role is not None:
        if role not in ROLES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "角色非法")
        if u.role != role:
            # 角色变更需超级管理员权限（由调用方在路由层校验），此处仅记录
            u.role = role
            changes["role"] = role
    if dept_group_id is not None:
        # 部门管理员只能把目标用户迁到自己范围内的分组
        if scope is not None and dept_group_id not in scope:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权将用户迁移到该部门")
        u.dept_group_id = dept_group_id
        changes["dept_group_id"] = dept_group_id
    db.commit()
    if changes:
        audit_log(db, actor, "user.update", "user", user_id, changes)
    db.refresh(u)
    return u


def assign_groups(
    db: Session,
    actor: int,
    user_id: int,
    group_ids: list[int],
    scope: set[int] | None = None,
) -> None:
    _check_scope(db, user_id, scope)
    _get(db, user_id)
    # 部门管理员只能分配其范围内的分组
    if scope is not None:
        for gid in group_ids:
            if gid not in scope:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "无权分配该分组")
    db.execute(delete(UserGroup).where(UserGroup.user_id == user_id))
    for gid in group_ids:
        db.add(UserGroup(user_id=user_id, group_id=gid))
    db.commit()
    audit_log(db, actor, "user.assign_groups", "user", user_id, {"group_ids": group_ids})


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
    db: Session,
    actor: int,
    email: str,
    name: str = "",
    role: str = "user",
    password: str | None = None,
    status_: str = "active",
    group_ids: list[int] | None = None,
) -> tuple[User, str]:
    """管理员手动新增用户。

    - email 唯一性校验；
    - password 由管理员提供，不在响应中返回；
    - role/status 白名单校验；
    - group_ids 合法性校验后写入关联；
    - 跳过邮箱验证流程（email_verified=True），管理员新增即视为可信账号。
    返回 (user, "")。
    """
    email = normalize_email(email)
    if not email or "@" not in email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邮箱格式不正确")
    if role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "角色非法")
    if status_ not in STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "状态非法")

    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该邮箱已存在")

    if not password or len(password) < 6:
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
    audit_log(
        db,
        actor,
        "user.create",
        "user",
        user.id,
        {
            "role": role,
            "status": status_,
            "group_ids": gids,
        },
    )
    return user, ""


def import_users(
    db: Session,
    actor: int,
    rows: list[dict],
    scope: set[int] | None = None,
    actor_role: str = "user",
) -> dict:
    """批量导入用户（管理员已预览确认）。rows 每项含 email/name/role/password/status/group_ids。

    单行失败不中断整体导入，逐行收集成功/失败计数与失败明细，统一提交成功项。
    - scope：部门管理员数据范围；非 None 时导入用户的 group_ids 必须落在 scope 内（防水平越权）。
    - actor_role：调用者角色；非 super_admin 不得导入管理员账号（防垂直越权，与 create_user 路由守卫一致）。
    """
    success = 0
    failed = 0
    errors: list[dict] = []
    pending_users: list[User] = []
    seen_emails: set[str] = set()
    for idx, r in enumerate(rows, start=1):
        email = normalize_email(str(r.get("email") or ""))
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
            # 仅超级管理员可导入管理员账号（垂直越权防护）
            if role in ("dept_admin", "super_admin") and actor_role != "super_admin":
                raise ValueError("仅超级管理员可创建管理员账号")
            st = str(r.get("status") or "active").strip() or "active"
            if st not in STATUSES:
                raise ValueError(f"状态非法: {st}")
            # 预览阶段已算好 bcrypt 哈希（明文不进入进程内缓存）。
            # 兼容直接调用本函数且只给明文的场景（如测试、脚本）。
            pwd_hash = str(r.get("password_hash") or "").strip()
            if not pwd_hash:
                pwd = str(r.get("password") or "").strip()
                if not pwd:
                    raise ValueError("初始密码不能为空")
                if len(pwd) < 6:
                    raise ValueError("密码至少 6 位")
                pwd_hash = hash_password(pwd)
            name = str(r.get("name") or "").strip() or email.split("@")[0]
            gids = _normalize_group_ids(db, r.get("group_ids"))
            # 部门管理员只能把导入用户分配到本部门子树内的分组（水平越权防护）
            if scope is not None:
                for gid in gids:
                    if gid not in scope:
                        raise ValueError("无权分配该分组")
            u = User(
                email=email,
                password_hash=pwd_hash,
                name=name,
                role=role,
                status=st,
                email_verified=True,
            )
            pending_users.append(u)
            # 临时挂在行上以便回填 id 与密码
            r["_user"] = u
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
            row_user = cast(User | None, r.get("_user"))
            if not row_user:
                continue
            for gid in r.get("_gids", []):
                db.add(UserGroup(user_id=row_user.id, group_id=gid))
    db.commit()
    audit_log(
        db,
        actor,
        "user.import",
        "user",
        0,
        {
            "success": success,
            "failed": failed,
            "total": len(rows),
        },
    )
    return {"success": success, "failed": failed, "errors": errors}
