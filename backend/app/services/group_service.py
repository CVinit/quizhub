"""分组业务：树形查询、增删改、子孙查询。"""

from __future__ import annotations

import logging

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.orm import Session

from app.core.deps import subtree_ids

# subtree_ids 统一由 core.deps 提供，避免「授权数据范围」与「成环检测」各持一份实现而漂移：
# 两份副本一旦不一致，可能导致越权可见或环检测失效。
from app.core.errors import DomainError
from app.core.status import BAD_REQUEST, NOT_FOUND
from app.models.exam import ExamDefinition, PaperTemplate
from app.models.group import GROUP_TYPE, Group, UserGroup
from app.models.user import User
from app.schemas.group import GroupCreate, GroupUpdate

logger = logging.getLogger("quizhub")

# 单一来源：复用模型层常量，避免分组类型白名单在两处漂移
GROUP_TYPES = GROUP_TYPE

__all__ = [
    "build_tree",
    "create_group",
    "delete_group",
    "subtree_ids",
    "update_group",
]


def build_tree(db: Session, scope: set[int] | None = None) -> list[dict]:
    """返回分组树形结构（带 children）。

    scope 非 None（部门管理员）时仅返回其子树内的分组，并按子树根重建树形，
    防止向部门管理员泄露其他部门的组织结构。
    """
    # scope 过滤下推到 SQL：部门管理员无需把整棵组织树载入内存再丢弃
    stmt = select(Group).order_by(Group.sort, Group.id)
    if scope is not None:
        stmt = stmt.where(Group.id.in_(scope))
    rows = db.execute(stmt).scalars().all()
    nodes: dict[int, dict] = {}
    # 第 1 遍只建节点并保留**原始** parent_id：此时 nodes 尚未建全，
    # 若在这里用 `parent_id in nodes` 判「是否为根」，会依赖行的遍历顺序
    # （rows 按 sort,id 全局排序，子节点 sort 小于祖先时会被误判为根），
    # 第 2 遍又把它挂进父节点 children —— 同一分组在响应里出现两次。
    for r in rows:
        nodes[r.id] = {
            "id": r.id,
            "name": r.name,
            "type": r.type,
            "parent_id": r.parent_id,
            "sort": r.sort,
            "children": [],
        }
    # 第 2 遍：构建父子关系；父节点不在本次 scope 内（或数据异常成环）的节点按根处理
    for r in rows:
        n = nodes[r.id]
        pid = r.parent_id
        if pid is not None and pid in nodes and pid != r.id:
            nodes[pid]["children"].append(n)
        else:
            n["parent_id"] = None
    roots: list[dict] = [n for n in nodes.values() if n["parent_id"] is None]
    return roots


def create_group(db: Session, payload: GroupCreate) -> Group:
    if payload.type not in GROUP_TYPES:
        raise DomainError(BAD_REQUEST, f"分组类型必须是 {GROUP_TYPES} 之一")
    if payload.parent_id is not None:
        parent = db.get(Group, payload.parent_id)
        if not parent:
            raise DomainError(BAD_REQUEST, "父分组不存在")
        # 新建分组无后代，不可能成环
    g = Group(name=payload.name, type=payload.type, parent_id=payload.parent_id, sort=payload.sort)
    db.add(g)
    db.commit()
    db.refresh(g)
    return g


def update_group(db: Session, group_id: int, payload: GroupUpdate) -> Group:
    g = db.get(Group, group_id)
    if not g:
        raise DomainError(NOT_FOUND, "分组不存在")
    data = payload.model_dump(exclude_unset=True)
    if "type" in data and data["type"] not in GROUP_TYPES:
        raise DomainError(BAD_REQUEST, f"分组类型必须是 {GROUP_TYPES} 之一")
    if "parent_id" in data and data["parent_id"] is not None:
        if data["parent_id"] == group_id:
            raise DomainError(BAD_REQUEST, "父分组不能是自身")
        if not db.get(Group, data["parent_id"]):
            raise DomainError(BAD_REQUEST, "父分组不存在")
        if _would_cycle(db, data["parent_id"], group_id):
            raise DomainError(BAD_REQUEST, "分组层级存在环")
    for k, v in data.items():
        setattr(g, k, v)
    db.commit()
    db.refresh(g)
    return g


def delete_group(db: Session, group_id: int) -> None:
    g = db.get(Group, group_id)
    if not g:
        raise DomainError(NOT_FOUND, "分组不存在")
    # 子孙存在则禁止删除
    if _has_children(db, group_id):
        raise DomainError(BAD_REQUEST, "存在子分组，请先删除子分组")
    # 题库/题目归属该分组则禁止删除，避免外键约束失败与数据孤儿
    if _has_question_banks(db, group_id):
        raise DomainError(BAD_REQUEST, "该分组下存在题库，请先迁移或解除题库归属")
    if _has_questions(db, group_id):
        raise DomainError(BAD_REQUEST, "该分组下存在题目，请先迁移或解除题目归属")
    # 解除用户关联
    db.execute(delete(UserGroup).where(UserGroup.group_id == group_id))
    # 部门管理员的归属部门引用该分组时必须置空。模型声明为 ON DELETE SET NULL，但
    # 既有库（迁移的 _TABLE_DDL 未覆盖 users 表）里实际是 NO ACTION，运行期
    # PRAGMA foreign_keys=ON 下直接删除会 FOREIGN KEY constraint failed → 500。
    # 显式置空既修复该路径，也让行为与模型声明一致。
    db.execute(update(User).where(User.dept_group_id == group_id).values(dept_group_id=None))
    # 剔除考试/模板指派里的悬空 JSON 引用（group_ids 是无外键的 JSON 数组）。注意
    # 剔除后**不能为空**：空指派 = 全员可见，会让「只指派给本分组」的考试静默升级为
    # 对所有人开放；剔除会删空时保留悬空 id（不可见，fail-closed）并记 WARNING，
    # 具体口径见 `_strip_group_from_assignments` 的 docstring。
    _strip_group_from_assignments(db, group_id)
    db.delete(g)
    db.commit()


def _rows_assigning_group(db: Session, model, group_id: int) -> list:
    """返回 group_ids 中含 `group_id` 的考试/模板行（SQL 侧用 json_each 判定包含）。

    原实现把两张表全量物化为 ORM 对象再在 Python 里逐行比较：在持有 SQLite 写锁的
    delete_group 事务内，考试/模板增长后会长时间占锁阻塞其它写请求。
    """
    json_values = func.json_each(model.group_ids).table_valued("value")
    stmt = select(model).where(
        model.group_ids.is_not(None),
        exists(select(1).select_from(json_values).where(json_values.c.value == group_id)),
    )
    return list(db.execute(stmt).scalars().all())


def _strip_group_from_assignments(db: Session, group_id: int) -> None:
    """从考试与试卷模板的指派分组中移除某个分组 id（JSON 列需整体赋新值）。

    **不变式：绝不能把 group_ids 清空。** 空指派在 `_user_can_access_exam` /
    `exam_in_scope` 里表示「不限（全员可见）」，而「只指派给被删分组」的考试一旦变成
    `[]`，就会从「仅该分组可见」静默升级为「所有人可见」—— 这是 fail-open 的信息泄露
    （已实跑复现）。因此剔除后若为空，保留悬空 id（该考试对所有人不可见，fail-closed），
    并记 WARNING 提示管理员重新指派。

    悬空 id 的副作用是 SQLite 复用被删分组 rowid 时新分组会继承可见性；该风险远小于
    「对全员开放」，且删除分组本身会提示管理员重新指派，故按 fail-closed 处理。
    """
    for model, label in ((ExamDefinition, "考试"), (PaperTemplate, "试卷模板")):
        for row in _rows_assigning_group(db, model, group_id):
            remaining = [gid for gid in (row.group_ids or []) if gid != group_id]
            if not remaining:
                logger.warning(
                    "[group] %s #%s 的指派分组被删空（仅指派给分组 %s），已保留悬空 id 以维持"
                    "「不可见」；请管理员重新指派，避免其变成全员可见",
                    label,
                    row.id,
                    group_id,
                )
                continue
            row.group_ids = remaining


def _would_cycle(db: Session, new_parent: int, group_id: int) -> bool:
    """把 group 的父设为 new_parent 是否形成环。"""
    ids = subtree_ids(db, group_id)
    return new_parent in ids


def _has_children(db: Session, group_id: int) -> bool:
    return db.execute(select(Group.id).where(Group.parent_id == group_id).limit(1)).first() is not None


def _has_question_banks(db: Session, group_id: int) -> bool:
    """该分组下是否存在题库（QuestionBank.group_id 引用）。"""
    from app.models.question import QuestionBank

    return db.execute(select(QuestionBank.id).where(QuestionBank.group_id == group_id).limit(1)).first() is not None


def _has_questions(db: Session, group_id: int) -> bool:
    """该分组下是否存在题目（Question.group_id 引用）。"""
    from app.models.question import Question

    return db.execute(select(Question.id).where(Question.group_id == group_id).limit(1)).first() is not None
