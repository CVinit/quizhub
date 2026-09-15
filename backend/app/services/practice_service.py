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


def enabled_bank_ids(db: Session) -> list[int]:
    """允许用户练习的题库 id 列表（practice_enabled=True）。

    练习入口的所有题量统计与抽题都必须限定在这些题库内；否则关闭某题库后，
    用户仍可通过「全部题库」范围练到它，开关形同虚设。

    公开导出：用户首页「总题数」等展示口径也必须复用它，保持全站一致。
    """
    from app.models.question import QuestionBank

    return [
        r[0]
        for r in db.execute(select(QuestionBank.id).where(QuestionBank.practice_enabled.is_(True))).all()
    ]


# 内部沿用的旧名（避免大规模改名）
_enabled_bank_ids = enabled_bank_ids


def _assert_bank_practice_enabled(db: Session, bank_id: int) -> None:
    """选定的题库必须处于开放练习状态，否则拒绝（防止直连 API 绕过前端隐藏）。"""
    from app.models.question import QuestionBank

    bank = db.get(QuestionBank, bank_id)
    if not bank or not bank.practice_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该题库未开放练习")


def _get_practice_question(db: Session, question_id: int) -> Question:
    """按 id 取题目并校验其题库处于开放练习状态。

    与 start_practice 的 _assert_bank_practice_enabled 保持同一道防线：
    否则用户只要知道 question_id，就能直连 /practice/answer 等接口作答并写入
    练习记录，绕过前端对已关闭题库的隐藏。bank_id 为空的孤儿题目不属于任何
    开放题库（enabled_bank_ids 不含 NULL），同样拒绝，与统计口径保持一致。
    """
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题目不存在")
    if q.bank_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该题库未开放练习")
    _assert_bank_practice_enabled(db, q.bank_id)
    return q


def list_modes(db: Session, user_id: int, bank_id: int | None = None) -> dict:
    """返回各模式可答题数统计（用 COUNT/GROUP BY 聚合，避免全量载入）。

    bank_id 非空时，total/type_dist/practiced/wrong/marked 均按该题库范围过滤；
    banks 返回开放练习的题库列表（供前端范围选择器渲染，不受范围影响）。

    所有统计仅计入 practice_enabled=True 的题库：包括「全部题库」范围，
    否则被关闭的题库仍会被计入总量。
    """
    from app.models.question import QuestionBank

    enabled = _enabled_bank_ids(db)
    # bank_id 由客户端传入，必须与 start_practice 一样校验该题库已开放练习，
    # 否则关闭的题库仍会通过统计接口泄露题量与题型分布。
    if bank_id:
        _assert_bank_practice_enabled(db, bank_id)
        scope_bank_ids: list[int] = [bank_id]
    else:
        # 没有任何开放题库时，范围限定为空集（返回 0 题），而非"不过滤"
        scope_bank_ids = enabled

    # 题量与题型分布：按范围过滤（None 表示全部开放题库）
    q_stmt = select(func.count(Question.id)).where(Question.bank_id.in_(scope_bank_ids))
    td_stmt = (
        select(Question.type, func.count(Question.id))
        .where(Question.bank_id.in_(scope_bank_ids))
        .group_by(Question.type)
    )
    total = db.execute(q_stmt).scalar() or 0
    type_dist: dict[str, int] = {r[0]: r[1] for r in db.execute(td_stmt).all()}

    # practiced/wrong/marked 来自 QuestionState（无 bank_id），JOIN Question 按范围过滤
    def _count_states(status_filter) -> int:
        stmt = (
            select(func.count(QuestionState.question_id))
            .join(Question, Question.id == QuestionState.question_id)
            .where(QuestionState.user_id == user_id, status_filter, Question.bank_id.in_(scope_bank_ids))
        )
        return db.execute(stmt).scalar() or 0

    practiced = _count_states(QuestionState.status != "unanswered")
    wrong = _count_states(QuestionState.status == "wrong")
    marked = _count_states(QuestionState.marked == True)  # noqa: E712

    # 各题库及其题量：仅返回开放练习的题库，供范围选择器渲染
    bank_rows = db.execute(
        select(QuestionBank.id, QuestionBank.name, func.count(Question.id))
        .join(Question, Question.bank_id == QuestionBank.id, isouter=True)
        .where(QuestionBank.practice_enabled.is_(True))
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

    bank_id 非空时，所有模式均限定在该题库范围内（实现"选题库后切子模式"）；
    为空时限定在全部「开放练习」的题库内（排除 practice_enabled=False 的题库）。
    """
    if mode not in PRACTICE_MODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"模式必须是 {PRACTICE_MODES} 之一")
    # 限制返回数量上限，避免无界加载
    if limit is None or limit > PRACTICE_LIMIT_MAX:
        limit = PRACTICE_LIMIT_MAX

    # 范围：指定题库需已开放练习；未指定则用全部开放题库（空列表表示无可用题库）
    if bank_id:
        _assert_bank_practice_enabled(db, bank_id)
        scope_bank_ids: list[int] = [bank_id]
    else:
        scope_bank_ids = _enabled_bank_ids(db)

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
        rows = _by_ids_with_bank(db, ids, scope_bank_ids)
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
        rows = _by_ids_with_bank(db, ids, scope_bank_ids)
    elif mode == "type":
        if not type_:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "按题型练习需指定题型")
        stmt = select(Question).where(Question.type == type_, Question.bank_id.in_(scope_bank_ids))
        rows = list(db.execute(stmt.order_by(Question.id).limit(limit)).scalars().all())
    elif mode == "random":
        # SQL 端随机抽样，避免把全库载入 Python 再 shuffle
        stmt = select(Question).where(Question.bank_id.in_(scope_bank_ids))
        rows = list(db.execute(stmt.order_by(func.random()).limit(limit)).scalars().all())
    else:  # sequence
        stmt = select(Question).where(Question.bank_id.in_(scope_bank_ids))
        rows = list(db.execute(stmt.order_by(Question.id).limit(limit)).scalars().all())

    return [_to_dict(q) for q in rows]


def _by_ids_with_bank(db: Session, ids: list[int], bank_ids: list[int]) -> list:
    """按题目 id 取题目，限定在给定题库范围内（用于错题本/标记模式的范围筛选）。"""
    if not ids or not bank_ids:
        return []
    stmt = select(Question).where(Question.id.in_(ids), Question.bank_id.in_(bank_ids))
    return list(db.execute(stmt).scalars().all())


def answer_question(db: Session, user_id: int, question_id: int, user_answer, mode: str) -> dict:
    q = _get_practice_question(db, question_id)
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
    """顺序练习进度：当前已练习数 + 总数（用 COUNT，避免全表载入）。

    与 list_modes 保持一致的口径：total 仅统计 practice_enabled=True 的题库，
    practiced 也必须 JOIN Question 限定在同一范围内——否则关闭某题库练习后，
    进度条会出现「已练 N / 总数 M」里 N 含被关闭题库、M 不含的分母/分子错配，
    甚至 practiced > total 导致进度超过 100%。
    """
    enabled = _enabled_bank_ids(db)
    total = db.execute(select(func.count(Question.id)).where(Question.bank_id.in_(enabled))).scalar() or 0
    practiced = (
        db.execute(
            select(func.count(QuestionState.question_id))
            .join(Question, Question.id == QuestionState.question_id)
            .where(
                QuestionState.user_id == user_id,
                QuestionState.status != "unanswered",
                Question.bank_id.in_(enabled),
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
    _get_practice_question(db, question_id)
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
    q = _get_practice_question(db, question_id)
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
