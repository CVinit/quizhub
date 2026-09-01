"""题库业务：题库来源、题目 CRUD、标签自动维护。"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.group import Group
from app.models.question import Question, QuestionBank, QuestionTag
from app.schemas.question import (
    QuestionBankCreate,
    QuestionCreate,
    QuestionUpdate,
)

QUESTION_TYPES = ("单选题", "多选题", "判断题", "填空题", "简答题", "拖拽题")


def _validate_group(db: Session, group_id: int | None, scope: set[int] | None) -> None:
    if group_id is not None and not db.get(Group, group_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "分组不存在")
    if scope is not None and (group_id is None or group_id not in scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "题目必须归属在可管理分组内")


def _get_allowed_bank(db: Session, bank_id: int | None, scope: set[int] | None) -> QuestionBank | None:
    if bank_id is None:
        return None
    bank = db.get(QuestionBank, bank_id)
    if not bank:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "题库不存在")
    if scope is not None and (bank.group_id is None or bank.group_id not in scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权操作该题库")
    return bank


def _validate_answer_shape(qtype: str, answer: object) -> None:
    """按题型校验答案形状，拒绝空答案（防止填空/拖拽空答案经 grade() 恒真被判满分）。

    - 单选：非空单字符字符串（含一个字母）
    - 多选：非空字符串（一个或多个字母）
    - 判断：'正确' 或 '错误'
    - 填空：非空 list 且每个空为非空 list[str]
    - 简答：非空字符串
    - 拖拽：非空 dict
    """
    if qtype in ("单选题", "多选题"):
        if not isinstance(answer, str) or not answer.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "选择题答案不能为空")
    elif qtype == "判断题":
        if answer not in ("正确", "错误"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "判断题答案必须是 正确/错误")
    elif qtype == "填空题":
        if not isinstance(answer, list) or not answer:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "填空题答案不能为空")
        for blanks in answer:
            if not isinstance(blanks, list) or not blanks:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "填空题每空至少需要一个等价答案")
    elif qtype == "简答题":
        if not isinstance(answer, str) or not answer.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "简答题参考答案不能为空")
    elif qtype == "拖拽题":  # noqa: SIM102  类型分派，嵌套 if 比合并成 and 更清晰
        if not isinstance(answer, dict) or not answer:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "拖拽题答案映射不能为空")


# ---------- 题库来源 ----------
def list_banks(db: Session, scope: set[int] | None = None) -> list[dict]:
    """返回全部题库，并附带各题库题目数（一次 GROUP BY 聚合，避免逐套 COUNT）。"""
    from sqlalchemy import func

    stmt = select(QuestionBank)
    if scope is not None:
        stmt = stmt.where(QuestionBank.group_id.in_(scope))
    rows = db.execute(stmt.order_by(QuestionBank.id.desc())).scalars().all()
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


def create_bank(db: Session, payload: QuestionBankCreate, scope: set[int] | None = None) -> QuestionBank:
    _validate_group(db, payload.group_id, scope)
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
    scope: set[int] | None = None,
) -> tuple[list[Question], int]:
    stmt = select(Question)
    if scope is not None:
        stmt = stmt.where(Question.group_id.in_(scope))
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


def create_question(db: Session, payload: QuestionCreate, scope: set[int] | None = None) -> Question:
    if payload.type not in QUESTION_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"题型必须是 {QUESTION_TYPES} 之一")
    _validate_answer_shape(payload.type, payload.answer)
    _validate_group(db, payload.group_id, scope)
    bank = _get_allowed_bank(db, payload.bank_id, scope)
    if bank and bank.group_id is not None and bank.group_id != payload.group_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "题目分组必须与题库分组一致")
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


def update_question(db: Session, qid: int, payload: QuestionUpdate, scope: set[int] | None = None) -> Question:
    q = db.get(Question, qid)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题目不存在")
    _validate_group(db, q.group_id, scope)
    data = payload.model_dump(exclude_unset=True)
    if "type" in data and data["type"] not in QUESTION_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"题型必须是 {QUESTION_TYPES} 之一")
    # 改题型或改答案时校验答案形状（杜绝空答案进入题库被判满分）
    effective_type = data.get("type", q.type)
    if "type" in data and "answer" not in data:
        _validate_answer_shape(effective_type, q.answer)
    if "group_id" in data:
        _validate_group(db, data["group_id"], scope)
    bank = _get_allowed_bank(db, data.get("bank_id", q.bank_id), scope)
    effective_group = data.get("group_id", q.group_id)
    if bank and bank.group_id is not None and bank.group_id != effective_group:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "题目分组必须与题库分组一致")
    if "answer" in data:
        _validate_answer_shape(effective_type, data["answer"])
    if data.get("tags"):
        _ensure_tags(db, data["tags"])
    for k, v in data.items():
        setattr(q, k, v)
    db.commit()
    db.refresh(q)
    return q


def delete_question(db: Session, qid: int, scope: set[int] | None = None) -> None:
    q = db.get(Question, qid)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题目不存在")
    _validate_group(db, q.group_id, scope)
    db.delete(q)
    db.commit()
