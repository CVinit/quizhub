"""练习业务：模式选题、逐题判分落库、题目状态、错题本、标记、简答自评。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.question import Question
from app.models.record import PracticeRecord, QuestionState
from app.services.grading import grade

PRACTICE_MODES = ("sequence", "random", "type", "wrong", "mark", "bank")
# 单次练习返回题量上限，避免全量加载 100k 题库
PRACTICE_LIMIT_MAX = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_modes(db: Session, user_id: int, bank_id: int | None = None) -> dict:
    """返回各模式可答题数统计（用 COUNT/GROUP BY 聚合，避免全量载入）。

    bank_id 非空时，total/type_dist/practiced/wrong/marked 均按该题库范围过滤；
    banks 始终返回全部题库列表（供前端范围选择器渲染，不受范围影响）。
    """
    from app.models.question import QuestionBank

    # 题量与题型分布：bank_id 非空时按范围过滤（None 表示全部题库，即总题库）
    q_stmt = select(func.count(Question.id))
    td_stmt = select(Question.type, func.count(Question.id)).group_by(Question.type)
    if bank_id:
        q_stmt = q_stmt.where(Question.bank_id == bank_id)
        td_stmt = td_stmt.where(Question.bank_id == bank_id)
    total = db.execute(q_stmt).scalar() or 0
    type_dist: dict[str, int] = {r[0]: r[1] for r in db.execute(td_stmt).all()}

    # practiced/wrong/marked 来自 QuestionState（无 bank_id），bank_id 非空时 JOIN Question 按范围过滤
    def _count_states(status_filter) -> int:
        stmt = select(func.count(QuestionState.question_id)).where(QuestionState.user_id == user_id, status_filter)
        if bank_id:
            stmt = stmt.join(Question, Question.id == QuestionState.question_id).where(Question.bank_id == bank_id)
        return db.execute(stmt).scalar() or 0

    practiced = _count_states(QuestionState.status != "unanswered")
    wrong = _count_states(QuestionState.status == "wrong")
    marked = _count_states(QuestionState.marked == True)  # noqa: E712

    # 各题库及其题量（一次 GROUP BY 聚合，始终全量，供范围选择器渲染）
    bank_rows = db.execute(
        select(QuestionBank.id, QuestionBank.name, func.count(Question.id))
        .join(Question, Question.bank_id == QuestionBank.id, isouter=True)
        .group_by(QuestionBank.id)
        .order_by(QuestionBank.id.desc())
    ).all()
    banks = [{"id": r[0], "name": r[1], "count": r[2]} for r in bank_rows]
    return {
        "total": total,
        "practiced": practiced,
        "wrong": wrong,
        "marked": marked,
        "type_dist": type_dist,
        "banks": banks,
    }


def start_practice(
    db: Session,
    user_id: int,
    mode: str,
    type_: str | None,
    limit: int | None,
    bank_id: int | None = None,
) -> list[dict]:
    """按模式返回练习题目。

    bank_id 非空时，所有模式均限定在该题库范围内（实现"选题库后切子模式"）。
    """
    if mode not in PRACTICE_MODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"模式必须是 {PRACTICE_MODES} 之一")
    # 限制返回数量上限，避免无界加载
    if limit is None or limit > PRACTICE_LIMIT_MAX:
        limit = PRACTICE_LIMIT_MAX

    rows: list[Question]
    if mode == "wrong":
        ids = [
            r[0]
            for r in db.execute(
                select(QuestionState.question_id)
                .where(QuestionState.user_id == user_id, QuestionState.status == "wrong")
                .limit(limit)
            ).all()
        ]
        rows = _by_ids_with_bank(db, ids, bank_id)
    elif mode == "mark":
        ids = [
            r[0]
            for r in db.execute(
                select(QuestionState.question_id)
                .where(
                    QuestionState.user_id == user_id,
                    QuestionState.marked == True,  # noqa: E712
                )
                .limit(limit)
            ).all()
        ]
        rows = _by_ids_with_bank(db, ids, bank_id)
    elif mode == "type":
        if not type_:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "按题型练习需指定题型")
        stmt = select(Question).where(Question.type == type_)
        if bank_id:
            stmt = stmt.where(Question.bank_id == bank_id)
        rows = list(db.execute(stmt.order_by(Question.id).limit(limit)).scalars().all())
    elif mode == "random":
        # SQL 端随机抽样，避免把全库载入 Python 再 shuffle
        stmt = select(Question)
        if bank_id:
            stmt = stmt.where(Question.bank_id == bank_id)
        rows = list(db.execute(stmt.order_by(func.random()).limit(limit)).scalars().all())
    else:  # sequence
        stmt = select(Question)
        if bank_id:
            stmt = stmt.where(Question.bank_id == bank_id)
        rows = list(db.execute(stmt.order_by(Question.id).limit(limit)).scalars().all())

    return [_to_dict(q) for q in rows]


def _by_ids_with_bank(db: Session, ids: list[int], bank_id: int | None) -> list:
    """按题目 id 取题目，可选按题库过滤（用于错题本/标记模式的题库范围筛选）。"""
    if not ids:
        return []
    stmt = select(Question).where(Question.id.in_(ids))
    if bank_id:
        stmt = stmt.where(Question.bank_id == bank_id)
    return list(db.execute(stmt).scalars().all())


def answer_question(db: Session, user_id: int, question_id: int, user_answer, mode: str) -> dict:
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题目不存在")
    is_correct = grade(q.type, q.answer, user_answer)
    now = _now()

    # 写练习记录
    db.add(
        PracticeRecord(
            user_id=user_id,
            question_id=question_id,
            bank_id=q.bank_id,
            mode=mode,
            user_answer=user_answer,
            is_correct=is_correct,
            self_eval=None,
            answered_at=now,
        )
    )

    # 更新题目状态
    db.execute(
        sqlite_insert(QuestionState)
        .values(
            user_id=user_id,
            question_id=question_id,
            status="unanswered",
            marked=False,
            marked_note="",
            answered_at=now,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "question_id"])
    )
    st = db.execute(
        select(QuestionState).where(QuestionState.user_id == user_id, QuestionState.question_id == question_id)
    ).scalar_one()

    if is_correct is True:
        st.status = "correct"
    elif is_correct is False:
        st.status = "wrong"
    # 简答 is_correct=None 时不动 status，等自评
    st.answered_at = now

    db.commit()
    return {
        "is_correct": is_correct,
        "correct_answer": q.answer,
        "analysis": q.analysis,
        "reference_answer": q.answer if q.type == "简答题" else None,
    }


def get_progress(db: Session, user_id: int) -> dict:
    """顺序练习进度：当前已练习数 + 总数（用 COUNT，避免全表载入）。"""
    total = db.execute(select(func.count(Question.id))).scalar() or 0
    practiced = (
        db.execute(
            select(func.count(QuestionState.question_id)).where(
                QuestionState.user_id == user_id, QuestionState.status != "unanswered"
            )
        ).scalar()
        or 0
    )
    return {"total": total, "practiced": practiced}


def recent_practice(db: Session, user_id: int, limit: int = 10) -> list[dict]:
    """最近练习记录（面板用）。"""
    rows = (
        db.execute(
            select(PracticeRecord)
            .where(PracticeRecord.user_id == user_id)
            .order_by(PracticeRecord.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    out = []
    for r in rows:
        q = db.get(Question, r.question_id)
        out.append(
            {
                "id": r.id,
                "question_id": r.question_id,
                "question": (q.question[:50] + "…")
                if q and len(q.question) > 50
                else (q.question if q else "（题目已删除）"),
                "type": q.type if q else "",
                "mode": r.mode,
                "is_correct": r.is_correct,
                "answered_at": r.answered_at,
            }
        )
    return out


def toggle_mark(db: Session, user_id: int, question_id: int, marked: bool, note: str) -> None:
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题目不存在")
    st = db.execute(
        select(QuestionState).where(QuestionState.user_id == user_id, QuestionState.question_id == question_id)
    ).scalar_one_or_none()
    if st is None:
        st = QuestionState(
            user_id=user_id,
            question_id=question_id,
            status="unanswered",
            marked=False,
            marked_note="",
            answered_at=_now(),
        )
        db.add(st)
    st.marked = marked
    st.marked_note = note
    db.commit()


def short_eval(db: Session, user_id: int, question_id: int, mastered: bool) -> None:
    """简答自评：掌握→correct 且移出错题本；需复习→wrong。"""
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题目不存在")
    if q.type != "简答题":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只有简答题可以自评")
    now = _now()
    # 更新最近的练习记录自评
    rec = db.execute(
        select(PracticeRecord)
        .where(PracticeRecord.user_id == user_id, PracticeRecord.question_id == question_id)
        .order_by(PracticeRecord.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if not rec:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先提交简答答案")
    if rec.self_eval is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该简答题已经自评")
    rec.self_eval = mastered
    rec.is_correct = mastered
    db.execute(
        sqlite_insert(QuestionState)
        .values(
            user_id=user_id,
            question_id=question_id,
            status="unanswered",
            marked=False,
            marked_note="",
            answered_at=now,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "question_id"])
    )
    st = db.execute(
        select(QuestionState).where(QuestionState.user_id == user_id, QuestionState.question_id == question_id)
    ).scalar_one()
    st.status = "correct" if mastered else "wrong"
    st.answered_at = now
    db.commit()


def _to_dict(q: Question) -> dict:
    return {
        "id": q.id,
        "type": q.type,
        "question": q.question,
        "options": q.options,
        "left_items": q.left_items,
        "right_items": q.right_items,
        "analysis": q.analysis,
        "difficulty": q.difficulty,
        "tags": q.tags,
        "score": q.score,
        "group_id": q.group_id,
    }
