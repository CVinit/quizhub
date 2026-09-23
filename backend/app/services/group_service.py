"""分组业务：树形查询、增删改、子孙查询。"""

from __future__ import annotations

from sqlalchemy import delete, select, update
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
    # 清理考试/模板指派里的悬空 JSON 引用：group_ids 是无外键的 JSON 数组，删除分组后
    # 残留的 id 会让 `_expand_groups` 找不到分组、而该分组的 user_groups 又已被清空，
    # 于是「只指派给这个分组的考试」对所有人静默不可见；管理端还会显示一个不在组织树
    # 里的分组 id。此外 SQLite 会复用被删的最大 rowid，新建分组可能继承旧 id 从而
    # 意外获得旧考试的可见性 —— 清掉引用后该风险一并消除。
    _strip_group_from_assignments(db, group_id)
    db.delete(g)
    db.commit()


def _strip_group_from_assignments(db: Session, group_id: int) -> None:
    """从考试与试卷模板的指派分组中移除某个分组 id（JSON 列需整体赋新值）。

    两个模型分别处理（而非遍历 `(ExamDefinition, PaperTemplate)`）：后者会让类型检查
    只看到公共基类 `PKMixin`，`group_ids` 属性不可见。
    """
    for row in db.execute(select(ExamDefinition).where(ExamDefinition.group_ids.is_not(None))).scalars().all():
        ids = list(row.group_ids or [])
        if group_id in ids:
            row.group_ids = [gid for gid in ids if gid != group_id]
    for tpl in db.execute(select(PaperTemplate).where(PaperTemplate.group_ids.is_not(None))).scalars().all():
        ids = list(tpl.group_ids or [])
        if group_id in ids:
            tpl.group_ids = [gid for gid in ids if gid != group_id]


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
