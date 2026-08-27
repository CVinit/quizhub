"""题库业务：题库来源、题目 CRUD、标签自动维护。"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.question import Question, QuestionBank, QuestionTag
from app.schemas.question import (
    QuestionBankCreate,
    QuestionCreate,
    QuestionUpdate,
)

QUESTION_TYPES = ("单选题", "多选题", "判断题", "填空题", "简答题", "拖拽题")


# ---------- 题库来源 ----------
def list_banks(db: Session) -> list[dict]:
    """返回全部题库，并附带各题库题目数（一次 GROUP BY 聚合，避免逐套 COUNT）。"""
    from sqlalchemy import func

    rows = db.execute(select(QuestionBank).order_by(QuestionBank.id.desc())).scalars().all()
    if not rows:
        return []
    bank_ids = [b.id for b in rows]
    cnt_rows = db.execute(
        select(Question.bank_id, func.count(Question.id))
        .where(Question.bank_id.in_(bank_ids))
        .group_by(Question.bank_id)
    ).all()
    cnt_map = {r[0]: r[1] for r in cnt_rows}
    return [{"id": b.id, "name": b.name, "group_id": b.group_id, "question_count": cnt_map.get(b.id, 0)} for b in rows]


def create_bank(db: Session, payload: QuestionBankCreate) -> QuestionBank:
    b = QuestionBank(name=payload.name, group_id=payload.group_id)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


# ---------- 题目 CRUD ----------
def list_questions(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    type_: str | None = None,
    bank_id: int | None = None,
    group_id: int | None = None,
    difficulty: int | None = None,
    keyword: str | None = None,
) -> tuple[list[Question], int]:
    stmt = select(Question)
    if type_:
        stmt = stmt.where(Question.type == type_)
    if bank_id:
        stmt = stmt.where(Question.bank_id == bank_id)
    if group_id:
        stmt = stmt.where(Question.group_id == group_id)
    if difficulty:
        stmt = stmt.where(Question.difficulty == difficulty)
    if keyword:
        stmt = stmt.where(Question.question.like(f"%{keyword}%"))
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar() or 0
    rows = db.execute(stmt.order_by(Question.id.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return list(rows), total


def _ensure_tags(db: Session, tags: list[str]) -> None:
    """确保标签存在。用 SAVEPOINT 包裹单条插入，避免回滚波及整个事务。"""
    for name in tags:
        if not name:
            continue
        exists = db.execute(select(QuestionTag).where(QuestionTag.name == name)).scalar_one_or_none()
        if not exists:
            try:
                with db.begin_nested():
                    db.add(QuestionTag(name=name))
            except IntegrityError:
                # 并发或重复插入触发唯一约束冲突；SAVEPOINT 已回滚，主事务不受影响
                pass


def create_question(db: Session, payload: QuestionCreate) -> Question:
    if payload.type not in QUESTION_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"题型必须是 {QUESTION_TYPES} 之一")
    if payload.tags:
        _ensure_tags(db, payload.tags)
    q = Question(
        bank_id=payload.bank_id,
        type=payload.type,
        question=payload.question,
        options=payload.options,
        left_items=payload.left_items,
        right_items=payload.right_items,
        answer=payload.answer,
        analysis=payload.analysis,
        difficulty=payload.difficulty,
        tags=payload.tags,
        score=payload.score,
        group_id=payload.group_id,
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    return q


def update_question(db: Session, qid: int, payload: QuestionUpdate) -> Question:
    q = db.get(Question, qid)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题目不存在")
    data = payload.model_dump(exclude_unset=True)
    if "type" in data and data["type"] not in QUESTION_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"题型必须是 {QUESTION_TYPES} 之一")
    if data.get("tags"):
        _ensure_tags(db, data["tags"])
    for k, v in data.items():
        setattr(q, k, v)
    db.commit()
    db.refresh(q)
    return q


def delete_question(db: Session, qid: int) -> None:
    q = db.get(Question, qid)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题目不存在")
    db.delete(q)
    db.commit()
