"""用户管理业务。"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import cast

from fastapi import status
from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.email import normalize_email
from app.core.errors import DomainError
from app.core.like import ESCAPE_CHAR, like_pattern
from app.core.security import hash_password
from app.models.group import Group, UserGroup
from app.models.user import USER_ROLE, USER_STATUS, User
from app.services.audit_service import log as audit_log

logger = logging.getLogger("quizhub")

# 角色与状态白名单：管理员新增/导入用户时校验，防伪造非法角色。
# 直接复用模型层常量，避免同一枚举在多处漂移（历史上曾有三份副本）。
ROLES = USER_ROLE
STATUSES = USER_STATUS
# 管理员角色集合：非超级管理员不得管理这些账号（见 _check_manage_permission）
ADMIN_ROLES = ("dept_admin", "super_admin")


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
        # % / _ 是 LIKE 通配符：不转义时搜 `_` 会命中任意单字符、搜 `%` 命中全表
        kw = like_pattern(keyword)
        stmt = stmt.where(or_(User.email.like(kw, escape=ESCAPE_CHAR), User.name.like(kw, escape=ESCAPE_CHAR)))
    if role:
        stmt = stmt.where(User.role == role)
    if status_:
        stmt = stmt.where(User.status == status_)
    if group_id:
        stmt = stmt.join(UserGroup, UserGroup.user_id == User.id).where(UserGroup.group_id == group_id)
    if scope is not None:
        # 部门管理员范围：交给 SQL 做 UNION，避免把成百上千个用户 id 物化成绑定参数
        # （SQLite 有参数上限，大部门下会直接报错）
        from app.core.deps import user_ids_subquery

        stmt = stmt.where(User.id.in_(user_ids_subquery(scope)))
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


def _check_scope(db: Session, actor: int, user_id: int, scope: set[int] | None) -> None:
    """部门管理员操作前校验目标用户落在其数据范围内，否则 403。

    拒绝时记一条 WARNING：越权探测（IDOR 枚举）此前在服务端不留任何痕迹，
    只有成功的管理操作才有审计。只记 id，不记邮箱等 PII。
    """
    from app.core.deps import user_in_scope

    if not user_in_scope(db, user_id, scope):
        logger.warning("[authz] 越权拒绝：actor=%s target_user=%s scope_size=%s", actor, user_id, len(scope or ()))
        raise DomainError(status.HTTP_403_FORBIDDEN, "无权操作该用户")


def _check_manage_permission(db: Session, actor: int, target: User) -> None:
    """非超级管理员不得对管理员账号执行管理操作（横向越权防护）。

    dept_admin 的数据范围按部门子树划定，同一子树内可能同时存在多个 dept_admin；
    若只看数据范围，同级管理员可以互相禁用、重置密码（也包括把自己锁死）。
    因此管理动作除了范围校验，还必须做角色层级校验：管理员账号只归超级管理员管。
    """
    actor_user = db.get(User, actor)
    if actor_user is None or (actor_user.role != "super_admin" and target.role in ADMIN_ROLES):
        logger.warning(
            "[authz] 越权拒绝：actor=%s target_user=%s target_role=%s",
            actor,
            target.id,
            target.role,
        )
        raise DomainError(status.HTTP_403_FORBIDDEN, "仅超级管理员可管理管理员账号")


def _other_active_super_admin_exists(exclude_id: int):
    """SQL 层面的 EXISTS 守卫：除 exclude_id 外仍有 active 超管。

    供「禁用/删除/降级」写成条件 UPDATE/DELETE 使用，把「读计数 → 判断 → 写」
    收敛成一条原子语句，避免并发请求各自认为对方仍可用而双双通过。
    """
    from sqlalchemy.orm import aliased

    other = aliased(User)
    return exists(select(other.id).where(other.role == "super_admin", other.status == "active", other.id != exclude_id))


def approve(db: Session, actor: int, user_id: int, scope: set[int] | None = None) -> User:
    _check_scope(db, actor, user_id, scope)
    u = _get(db, user_id)
    _check_manage_permission(db, actor, u)
    if u.status != "pending":
        raise DomainError(status.HTTP_400_BAD_REQUEST, "该用户非待审批状态")
    u.status = "active"
    db.commit()
    audit_log(db, actor, "user.approve", "user", user_id)
    db.refresh(u)
    return u


def set_status(db: Session, actor: int, user_id: int, enabled: bool, scope: set[int] | None = None) -> User:
    _check_scope(db, actor, user_id, scope)
    u = _get(db, user_id)
    _check_manage_permission(db, actor, u)
    # 与 delete_user 同级的兜底：禁用是非 active 用户被 get_current_user 直接 403，
    # 且恢复角色只允许超管，因此把自己/最后一个 active 超管禁用会让系统永久失去管理入口。
    if not enabled and u.role == "super_admin" and u.status == "active":
        if actor == user_id:
            raise DomainError(status.HTTP_400_BAD_REQUEST, "不能禁用当前登录账号")
        # 原子守卫：并发禁用两个互为「最后一个」的超管时，先到者成功、后到者匹配 0 行 → 400，
        # 不会出现两边都读到「对方仍 active」而最终 0 个可用超管的锁死状态。
        # 不再先跑一次 COUNT 预检查：WHERE 里的 EXISTS 与随后的 rowcount 才是权威判据，
        # 预检查既多一次查询，并发下还可能给出与守卫不同的结论。
        guarded = cast(
            CursorResult,
            db.execute(
                update(User)
                .where(User.id == user_id, User.status == "active", _other_active_super_admin_exists(user_id))
                .values(status="disabled")
                .execution_options(synchronize_session=False)
            ),
        )
        if guarded.rowcount == 0:
            raise DomainError(status.HTTP_400_BAD_REQUEST, "不能禁用最后一个超级管理员账号")
        db.commit()
        audit_log(db, actor, "user.disable", "user", user_id)
        db.refresh(u)
        return u
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
        raise DomainError(status.HTTP_403_FORBIDDEN, "仅超级管理员可删除用户")
    if actor == user_id:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "不能删除当前登录账号")
    _check_scope(db, actor, user_id, scope)
    u = _get(db, user_id)
    if u.role == "super_admin":
        remaining = db.execute(
            select(func.count()).select_from(User).where(User.role == "super_admin", User.id != user_id)
        ).scalar_one()
        if remaining == 0:
            raise DomainError(status.HTTP_400_BAD_REQUEST, "不能删除最后一个超级管理员账号")

    # 本人考试会话 → 成绩 → 简答复核，按外键依赖顺序清理
    session_ids = [r[0] for r in db.execute(select(ExamSession.id).where(ExamSession.user_id == user_id)).all()]
    if session_ids:
        result_ids = select(ExamResult.id).where(ExamResult.exam_session_id.in_(session_ids)).scalar_subquery()
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
    # 删除同样加原子守卫：与并发的禁用/删除请求竞争时，只有一方能真正删掉最后的管理入口。
    # 前面的级联清理都在同一事务内，守卫失败时整体回滚，不留半损状态。
    removed = cast(
        CursorResult,
        db.execute(
            delete(User)
            .where(
                User.id == user_id,
                or_(User.role != "super_admin", User.status != "active", _other_active_super_admin_exists(user_id)),
            )
            .execution_options(synchronize_session="fetch")
        ),
    )
    if removed.rowcount == 0:
        db.rollback()
        raise DomainError(status.HTTP_400_BAD_REQUEST, "不能删除最后一个超级管理员账号")
    db.commit()
    # 审计只记非 PII 标识：邮箱在应用日志/排行榜/邮件异常各处均已脱敏，
    # 审计明细也不应成为唯一泄露点（与 user.create 的明细口径一致）。
    audit_log(db, actor, "user.delete", "user", user_id, {"role": u.role})


def reset_password(
    db: Session,
    actor: int,
    user_id: int,
    new_password: str,
    scope: set[int] | None = None,
) -> None:
    """重置密码。新密码由管理员经安全渠道提供，本函数不回传明文。

    返回 None 而非明文：避免调用方不慎把口令透传到响应体（明文由调用方持有，
    本就无需回传）。
    """
    _check_scope(db, actor, user_id, scope)
    u = _get(db, user_id)
    _check_manage_permission(db, actor, u)
    if len(new_password) < 6:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "密码至少 6 位")
    u.password_hash = hash_password(new_password)
    u.token_version += 1
    db.commit()
    audit_log(db, actor, "user.reset_password", "user", user_id)


def update_user(
    db: Session,
    actor: int,
    user_id: int,
    name: str | None,
    role: str | None,
    dept_group_id: int | None,
    scope: set[int] | None = None,
    *,
    clear_dept_group: bool = False,
) -> User:
    """更新用户。角色变更仅超级管理员可执行；部门管理员不得修改角色。

    `clear_dept_group=True` 表示调用方显式要求清空部门归属（HTTP 层收到
    `dept_group_id: null`）；否则 `dept_group_id=None` 保持"不修改"语义。
    """
    _check_scope(db, actor, user_id, scope)
    u = _get(db, user_id)
    _check_manage_permission(db, actor, u)
    changes: dict = {}
    if name is not None:
        u.name = name
        changes["name"] = name
    if role is not None:
        if role not in ROLES:
            raise DomainError(status.HTTP_400_BAD_REQUEST, "角色非法")
        if u.role != role:
            # 降级最后一个 active 超管同样会永久锁死管理入口，与 delete_user 保持一致拒绝
            if u.role == "super_admin" and role != "super_admin" and u.status == "active":
                # 原子守卫（并发降级/禁用竞争）；rowcount 是权威判据，无需前置 COUNT
                guarded = cast(
                    CursorResult,
                    db.execute(
                        update(User)
                        .where(
                            User.id == user_id, User.role == "super_admin", _other_active_super_admin_exists(user_id)
                        )
                        .values(role=role)
                        .execution_options(synchronize_session=False)
                    ),
                )
                if guarded.rowcount == 0:
                    raise DomainError(status.HTTP_400_BAD_REQUEST, "不能降级最后一个超级管理员账号")
                changes["role"] = role
            else:
                # 角色变更需超级管理员权限（由调用方在路由层校验），此处仅记录
                u.role = role
                changes["role"] = role
    if clear_dept_group:
        u.dept_group_id = None
        changes["dept_group_id"] = None
    elif dept_group_id is not None:
        # 部门管理员只能把目标用户迁到自己范围内的分组
        if scope is not None and dept_group_id not in scope:
            raise DomainError(status.HTTP_403_FORBIDDEN, "无权将用户迁移到该部门")
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
    """全量替换某用户的分组关联。

    校验（分组存在、去重、数据范围）必须在删除旧关联**之前**完成，
    否则一次非法请求会先清空用户既有分组、再因外键/唯一约束抛 500，留下半损状态。
    """
    _check_scope(db, actor, user_id, scope)
    _check_manage_permission(db, actor, _get(db, user_id))
    gids = list(dict.fromkeys(g for g in group_ids if g))
    if gids:
        valid = {r[0] for r in db.execute(select(Group.id).where(Group.id.in_(gids))).all()}
        missing = [gid for gid in gids if gid not in valid]
        if missing:
            raise DomainError(status.HTTP_400_BAD_REQUEST, f"分组不存在: {missing}")
    # 部门管理员只能分配其范围内的分组
    if scope is not None:
        for gid in gids:
            if gid not in scope:
                raise DomainError(status.HTTP_403_FORBIDDEN, "无权分配该分组")
    db.execute(delete(UserGroup).where(UserGroup.user_id == user_id))
    for gid in gids:
        db.add(UserGroup(user_id=user_id, group_id=gid))
    db.commit()
    audit_log(db, actor, "user.assign_groups", "user", user_id, {"group_ids": gids})


def _get(db: Session, user_id: int) -> User:
    u = db.get(User, user_id)
    if not u:
        raise DomainError(status.HTTP_404_NOT_FOUND, "用户不存在")
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
    dept_group_id: int | None = None,
) -> User:
    """管理员手动新增用户。

    - email 唯一性校验；
    - password 由管理员提供，不在响应中返回；
    - role/status 白名单校验；
    - group_ids 合法性校验后写入关联；
    - dept_group_id：部门管理员建号时自动归属其部门（与创建同一事务写入，
      避免调用方二次 commit 造成"用户已建、归属缺失"的半损状态）；
    - 跳过邮箱验证流程（email_verified=True），管理员新增即视为可信账号。
    """
    email = normalize_email(email)
    if not email or "@" not in email:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "邮箱格式不正确")
    if role not in ROLES:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "角色非法")
    if status_ not in STATUSES:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "状态非法")

    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "该邮箱已存在")

    if not password or len(password) < 6:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "密码至少 6 位")

    gids = _normalize_group_ids(db, group_ids)
    user = User(
        email=email,
        password_hash=hash_password(password),
        name=(name or "").strip() or email.split("@")[0],
        role=role,
        status=status_,
        email_verified=True,
        dept_group_id=dept_group_id,
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
    return user


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

    性能与隔离：
    - 已存在邮箱、合法分组 id 均一次性预取，消除逐行 SELECT（原实现每行 2 次查询）；
    - 每行插入用 SAVEPOINT 隔离，并发导致的唯一约束冲突只让该行失败，不会整批 500。
    """
    success = 0
    failed = 0
    errors: list[dict] = []
    pending: list[tuple[int, User, list[int]]] = []
    seen_emails: set[str] = set()

    # 一次性预取：本批涉及的邮箱中已存在的部分
    candidate_emails = {normalize_email(str(r.get("email") or "")) for r in rows} - {""}
    existing_emails = (
        {row[0] for row in db.execute(select(User.email).where(User.email.in_(candidate_emails))).all()}
        if candidate_emails
        else set()
    )
    # 一次性预取：本批涉及的分组 id 中真实存在的部分
    candidate_gids: set[int] = set()
    for r in rows:
        for gid in r.get("group_ids") or []:
            try:
                value = int(gid)
            except (TypeError, ValueError):
                continue
            if value > 0:
                candidate_gids.add(value)
    valid_gids = (
        {row[0] for row in db.execute(select(Group.id).where(Group.id.in_(candidate_gids))).all()}
        if candidate_gids
        else set()
    )

    for idx, r in enumerate(rows, start=1):
        email = normalize_email(str(r.get("email") or ""))
        try:
            if not email or "@" not in email:
                raise ValueError("邮箱格式不正确")
            if email in seen_emails:
                raise ValueError("本次导入内邮箱重复")
            seen_emails.add(email)
            if email in existing_emails:
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
                if len(pwd.encode("utf-8")) > 72:
                    raise ValueError("密码 UTF-8 编码后不能超过 72 字节")
                pwd_hash = hash_password(pwd)
            name = str(r.get("name") or "").strip() or email.split("@")[0]
            # 去重 + 只保留真实存在的分组（与模板说明的「非法分组ID将被忽略」一致）
            gids = list(dict.fromkeys(gid for gid in (r.get("group_ids") or []) if gid in valid_gids))
            # 部门管理员只能把导入用户分配到本部门子树内的分组（水平越权防护）
            if scope is not None:
                for gid in gids:
                    if gid not in scope:
                        raise ValueError("无权分配该分组")
            pending.append(
                (
                    idx,
                    User(
                        email=email,
                        password_hash=pwd_hash,
                        name=name,
                        role=role,
                        status=st,
                        email_verified=True,
                    ),
                    gids,
                )
            )
        except (ValueError, TypeError) as e:  # 行数据非法：都是本函数主动抛出的受控文案
            failed += 1
            errors.append({"row": idx, "email": email, "error": str(e)})
        except Exception:  # noqa: BLE001  未预期错误：记堆栈，对外只回受控文案
            logger.exception("[user_import] 第 %s 行解析出现未预期错误", idx)
            failed += 1
            errors.append({"row": idx, "email": email, "error": "导入失败，请检查模板或联系管理员"})

    # 逐行 SAVEPOINT 插入：唯一约束竞态只影响该行，其余行照常提交
    for idx, user, gids in pending:
        try:
            with db.begin_nested():
                db.add(user)
                db.flush()
                for gid in gids:
                    db.add(UserGroup(user_id=user.id, group_id=gid))
        except SQLAlchemyError as e:  # 落库阶段失败（如并发建号撞唯一约束）
            # 不透传驱动原文：IntegrityError 的消息含 SQL 片段、列名、约束名
            logger.warning("[user_import] 第 %s 行写入失败：%s", idx, type(e).__name__)
            failed += 1
            errors.append({"row": idx, "email": user.email, "error": "写入失败（邮箱可能已被占用）"})
            continue
        except Exception:  # noqa: BLE001  未预期错误：记堆栈，对外只回受控文案
            logger.exception("[user_import] 第 %s 行写入出现未预期错误", idx)
            failed += 1
            errors.append({"row": idx, "email": user.email, "error": "写入失败，请稍后重试或联系管理员"})
            continue
        success += 1
    db.commit()
    errors.sort(key=lambda item: item["row"])
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
