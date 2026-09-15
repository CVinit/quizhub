"""题库业务：题库来源、题目 CRUD、标签自动维护。"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.exam import ExamQuestion
from app.models.group import Group
from app.models.question import Question, QuestionBank, QuestionTag
from app.models.record import PracticeRecord, QuestionState
from app.schemas.question import (
    QuestionBankCreate,
    QuestionBankUpdate,
    QuestionCreate,
    QuestionUpdate,
)

QUESTION_TYPES = ("单选题", "多选题", "判断题", "填空题", "简答题", "拖拽题")

# update_question 允许客户端修改的字段白名单。
# 与 QuestionUpdate schema 的字段保持一致，但作为服务层的独立防线：
# 任何未列入此处的键都会被拒绝，避免 schema 演进时出现静默的批量赋值漏洞。
QUESTION_UPDATABLE_FIELDS = frozenset(
    {
        "type",
        "question",
        "options",
        "left_items",
        "right_items",
        "answer",
        "analysis",
        "difficulty",
        "tags",
        "score",
        "group_id",
        "bank_id",
    }
)


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
def list_banks(db: Session, scope: set[int] | None = None, practice_enabled: bool | None = None) -> list[dict]:
    """返回题库，并附带各题库题目数（一次 GROUP BY 聚合，避免逐套 COUNT）。

    practice_enabled 为 None 时返回全部题库（管理端默认：需看到并管理已关闭的题库）；
    显式传 True/False 时按「开放练习 / 仅考试使用」筛选。
    """
    from sqlalchemy import func

    stmt = select(QuestionBank)
    if scope is not None:
        stmt = stmt.where(QuestionBank.group_id.in_(scope))
    if practice_enabled is not None:
        stmt = stmt.where(QuestionBank.practice_enabled.is_(practice_enabled))
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
    return [
        {
            "id": b.id,
            "name": b.name,
            "group_id": b.group_id,
            "question_count": cnt_map.get(b.id, 0),
            "practice_enabled": bool(b.practice_enabled),
        }
        for b in rows
    ]


def create_bank(db: Session, payload: QuestionBankCreate, scope: set[int] | None = None) -> QuestionBank:
    _validate_group(db, payload.group_id, scope)
    b = QuestionBank(name=payload.name, group_id=payload.group_id, practice_enabled=payload.practice_enabled)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def update_bank(db: Session, bank_id: int, payload: QuestionBankUpdate, scope: set[int] | None = None) -> QuestionBank:
    """更新题库（改名 / 练习开关）。练习开关只影响后续练习入口，历史记录保留。"""
    b = _get_allowed_bank(db, bank_id, scope)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题库不存在")
    if payload.name is not None:
        b.name = payload.name
    if payload.practice_enabled is not None:
        b.practice_enabled = payload.practice_enabled
    db.commit()
    db.refresh(b)
    return b


def delete_bank(db: Session, bank_id: int, scope: set[int] | None = None) -> None:
    """删除题库及其题目。

    若其中任一题目已被考试引用（exam_questions），拒绝删除以保持历史考试可追溯；
    此时管理员可改用「关闭练习」。
    """
    b = _get_allowed_bank(db, bank_id, scope)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题库不存在")
    qids = [r[0] for r in db.execute(select(Question.id).where(Question.bank_id == bank_id)).all()]
    if qids:
        used = db.execute(
            select(func.count()).select_from(ExamQuestion).where(ExamQuestion.question_id.in_(qids))
        ).scalar_one()
        if used:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"该题库有 {used} 道题被考试引用，无法删除；可改为关闭练习",
            )
        # 先清理引用这些题目的练习状态，避免留下孤儿数据
        db.execute(delete(PracticeRecord).where(PracticeRecord.question_id.in_(qids)))
        db.execute(delete(QuestionState).where(QuestionState.question_id.in_(qids)))
        db.execute(delete(Question).where(Question.id.in_(qids)))
    db.delete(b)
    db.commit()


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
    # 白名单写入：不依赖 Pydantic schema 的字段列表兜底。
    # 否则日后给 QuestionUpdate 增加任何字段（例如 id/bank_id 之类）都会
    # 直接变成客户端可写，且改动发生在另一层、评审时不易察觉。
    for key, value in data.items():
        if key not in QUESTION_UPDATABLE_FIELDS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"不允许修改字段: {key}")
        setattr(q, key, value)
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


def type_stats(
    db: Session,
    bank_ids: list[int] | None = None,
    group_ids: list[int] | None = None,
    tags: list[str] | None = None,
    scope: set[int] | None = None,
) -> dict:
    """按组卷来源条件统计各题型可用题量（与 paper_service 的候选筛选口径一致）。

    用于题型配比编辑时提示“每个题型还剩多少题可选”，配额超过可用量时前端可即时预警。
    """
    stmt = select(Question.id, Question.type, Question.tags)
    if scope is not None:
        stmt = stmt.where(Question.group_id.in_(scope))
    if bank_ids:
        stmt = stmt.where(Question.bank_id.in_(bank_ids))
    if group_ids:
        stmt = stmt.where(Question.group_id.in_(group_ids))
    if tags:
        stmt = stmt.where(Question.tags.contains(tags))  # JSON 包含，近似
    rows = db.execute(stmt).all()
    # tags 用 Python 端精确过滤兜底（SQLite JSON 查询能力有限），与组卷逻辑保持一致
    if tags:
        rows = [r for r in rows if r[2] and any(t in r[2] for t in tags)]
    counts = {t: 0 for t in QUESTION_TYPES}
    for r in rows:
        if r[1] in counts:
            counts[r[1]] += 1
    return counts
