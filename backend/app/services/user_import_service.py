"""用户批量导入：Excel 预览确认后的落库逻辑。

从 `user_service` 拆出：该文件混合了列表查询、权限校验、单条 CRUD、级联删除与批量导入
五类职责，有效代码超过 500 行红线。导入自身的复杂度也不低（一次性预取、逐行校验、
SAVEPOINT 隔离写入、错误汇总与审计），独立成模块后两侧都更易读、更易测试。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.email import normalize_email
from app.core.security import hash_password, validate_password_bytes
from app.models.group import Group, UserGroup
from app.models.user import (
    ROLE_DEPT_ADMIN,
    ROLE_SUPER_ADMIN,
    ROLE_USER,
    STATUS_ACTIVE,
    USER_ROLE,
    USER_STATUS,
    User,
)
from app.services.audit_service import log as audit_log

logger = logging.getLogger("quizhub")

# 角色与状态白名单：直接复用模型层常量，避免同一枚举在多处漂移
# （与 user_service 的 ROLES/STATUSES 同源）。
ROLES = USER_ROLE
STATUSES = USER_STATUS

# 分组 id 预取的分块大小：Excel 单元格上限 1 万字符（约 2500 个 id/行）、单次最多 5000 行，
# 一条 `IN (...)` 理论上能塞进数十万个绑定参数并撞上 SQLite 的变量上限（too many SQL variables）。
_GID_PREFETCH_CHUNK = 500


def import_users(
    db: Session,
    actor: int,
    rows: list[dict],
    scope: set[int] | None = None,
    actor_role: str = ROLE_USER,
    dept_group_id: int | None = None,
) -> dict:
    """批量导入用户（管理员已预览确认）。rows 每项含 email/name/role/password/status/group_ids。

    单行失败不中断整体导入，逐行收集成功/失败计数与失败明细，统一提交成功项。
    - scope：部门管理员数据范围；非 None 时导入用户的 group_ids 必须落在 scope 内（防水平越权）。
    - actor_role：调用者角色；非 super_admin 不得导入管理员账号（防垂直越权，与 create_user 路由守卫一致）。
    - dept_group_id：新用户的归属部门（部门管理员导入时传其本部门，与 create_user 同口径）；
      模板的「分组ID」列允许留空，若不自动归属，导入出的用户不在调用者数据范围内，
      会出现「自己建的号自己看不到、管不了」的孤儿用户。

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
    valid_gids: set[int] = set()
    candidate_list = sorted(candidate_gids)
    for start in range(0, len(candidate_list), _GID_PREFETCH_CHUNK):
        chunk = candidate_list[start : start + _GID_PREFETCH_CHUNK]
        valid_gids |= {row[0] for row in db.execute(select(Group.id).where(Group.id.in_(chunk))).all()}

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
            role = str(r.get("role") or ROLE_USER).strip() or ROLE_USER
            if role not in ROLES:
                raise ValueError(f"角色非法: {role}")
            # 仅超级管理员可导入管理员账号（垂直越权防护）
            if role in (ROLE_DEPT_ADMIN, ROLE_SUPER_ADMIN) and actor_role != ROLE_SUPER_ADMIN:
                raise ValueError("仅超级管理员可创建管理员账号")
            st = str(r.get("status") or STATUS_ACTIVE).strip() or STATUS_ACTIVE
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
                validate_password_bytes(pwd)  # bcrypt 上限统一由 core.security 判定
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
                        dept_group_id=dept_group_id,
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
