"""分组业务：树形查询、增删改、子孙查询。"""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.group import Group, UserGroup
from app.schemas.group import GroupCreate, GroupUpdate


GROUP_TYPES = ("部门", "专业", "班级", "自定义")


def build_tree(db: Session) -> list[dict]:
    """返回分组树形结构（带 children）。"""
    rows = db.execute(select(Group).order_by(Group.sort, Group.id)).scalars().all()
    nodes: dict[int, dict] = {}
    for r in rows:
        nodes[r.id] = {
            "id": r.id, "name": r.name, "type": r.type,
            "parent_id": r.parent_id, "sort": r.sort, "children": [],
        }
    roots: list[dict] = []
    for n in nodes.values():
        if n["parent_id"] and n["parent_id"] in nodes:
            nodes[n["parent_id"]]["children"].append(n)
        else:
            roots.append(n)
    return roots


def create_group(db: Session, payload: GroupCreate) -> Group:
    if payload.type not in GROUP_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"分组类型必须是 {GROUP_TYPES} 之一")
    if payload.parent_id is not None:
        parent = db.get(Group, payload.parent_id)
        if not parent:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "父分组不存在")
        # 新建分组无后代，不可能成环
    g = Group(name=payload.name, type=payload.type, parent_id=payload.parent_id, sort=payload.sort)
    db.add(g)
    db.commit()
    db.refresh(g)
    return g


def update_group(db: Session, group_id: int, payload: GroupUpdate) -> Group:
    g = db.get(Group, group_id)
    if not g:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "分组不存在")
    data = payload.model_dump(exclude_unset=True)
    if "type" in data and data["type"] not in GROUP_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"分组类型必须是 {GROUP_TYPES} 之一")
    if "parent_id" in data and data["parent_id"] is not None:
        if data["parent_id"] == group_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "父分组不能是自身")
        if not db.get(Group, data["parent_id"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "父分组不存在")
        if _would_cycle(db, data["parent_id"], group_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "分组层级存在环")
    for k, v in data.items():
        setattr(g, k, v)
    db.commit()
    db.refresh(g)
    return g


def delete_group(db: Session, group_id: int) -> None:
    g = db.get(Group, group_id)
    if not g:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "分组不存在")
    # 子孙存在则禁止删除
    if _has_children(db, group_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "存在子分组，请先删除子分组")
    # 题库/题目归属该分组则禁止删除，避免外键约束失败与数据孤儿
    if _has_question_banks(db, group_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该分组下存在题库，请先迁移或解除题库归属")
    if _has_questions(db, group_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该分组下存在题目，请先迁移或解除题目归属")
    # 解除用户关联
    db.execute(UserGroup.__table__.delete().where(UserGroup.group_id == group_id))
    db.delete(g)
    db.commit()


def subtree_ids(db: Session, group_id: int) -> set[int]:
    """返回某分组及其全部后代 id。"""
    ids: set[int] = {group_id}
    stack = [group_id]
    while stack:
        parent = stack.pop()
        children = db.execute(
            select(Group.id).where(Group.parent_id == parent)
        ).scalars().all()
        for cid in children:
            if cid not in ids:
                ids.add(cid)
                stack.append(cid)
    return ids


def _would_cycle(db: Session, new_parent: int, group_id: int) -> bool:
    """把 group 的父设为 new_parent 是否形成环。"""
    ids = subtree_ids(db, group_id)
    return new_parent in ids


def _has_children(db: Session, group_id: int) -> bool:
    return db.execute(select(Group.id).where(Group.parent_id == group_id).limit(1)).first() is not None


def _has_question_banks(db: Session, group_id: int) -> bool:
    """该分组下是否存在题库（QuestionBank.group_id 引用）。"""
    from app.models.question import QuestionBank
    return db.execute(
        select(QuestionBank.id).where(QuestionBank.group_id == group_id).limit(1)
    ).first() is not None


def _has_questions(db: Session, group_id: int) -> bool:
    """该分组下是否存在题目（Question.group_id 引用）。"""
    from app.models.question import Question
    return db.execute(
        select(Question.id).where(Question.group_id == group_id).limit(1)
    ).first() is not None
