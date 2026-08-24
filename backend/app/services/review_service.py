"""简答复核业务：待复核列表、逐题复核、公布成绩。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.record import ExamResult, ShortAnswerReview
from app.models.user import User


def list_pending(db: Session) -> list[dict]:
    rows = db.execute(
        select(ShortAnswerReview).where(ShortAnswerReview.verdict.is_(None)).order_by(ShortAnswerReview.id.desc())
    ).scalars().all()
    out = []
    for r in rows:
        result = db.get(ExamResult, r.exam_result_id)
        from app.models.exam import ExamDefinition
        e = db.get(ExamDefinition, result.exam_definition_id) if result else None
        from app.models.question import Question
        q = db.get(Question, r.question_id) if r.question_id else None
        from app.models.user import User as U
        u = db.get(U, r.user_id) if r.user_id else None
        out.append({
            "id": r.id, "exam_result_id": r.exam_result_id,
            "exam_name": e.name if e else "",
            "user": u.email if u else "",
            "question_id": r.question_id,
            "question": q.question[:60] if q else "",
            "user_answer": r.user_answer, "reference_answer": r.reference_answer,
        })
    return out


def review(db: Session, review_id: int, verdict: str, partial_score: float | None, reviewer: User) -> dict:
    if verdict not in ("pass", "fail", "partial"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "verdict 必须是 pass/fail/partial")
    r = db.get(ShortAnswerReview, review_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "复核记录不存在")
    if r.verdict is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该题已复核")
    r.verdict = verdict
    r.partial_score = partial_score if verdict == "partial" else None
    r.reviewer = reviewer.id
    r.reviewed_at = datetime.now(timezone.utc).isoformat()
    db.commit()

    # 计入成绩
    result = db.get(ExamResult, r.exam_result_id)
    if result:
        eqs = _get_exam_question_score(db, result.exam_definition_id, r.question_id)
        if verdict == "pass":
            result.score += eqs
        elif verdict == "partial" and partial_score is not None:
            result.score += partial_score
        db.commit()
    return {"success": True, "verdict": verdict}


def _get_exam_question_score(db: Session, exam_id: int, qid: int) -> float:
    from app.models.exam import ExamQuestion
    eq = db.execute(
        select(ExamQuestion).where(
            ExamQuestion.exam_definition_id == exam_id, ExamQuestion.question_id == qid
        )
    ).scalar_one_or_none()
    return eq.score if eq else 2.0


def publish_results(db: Session, exam_id: int) -> dict:
    """公布某考试所有成绩（要求该考试所有简答已复核）。

    同时处理「无简答但 show_score_immediately=False」的成绩公布场景：
    这类成绩在交卷时 published=False，需经此接口由管理员手动公布。
    """
    from app.models.exam import ExamDefinition
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试不存在")
    results = db.execute(
        select(ExamResult).where(ExamResult.exam_definition_id == exam_id, ExamResult.published == False)  # noqa: E712
    ).scalars().all()
    # 一次查询所有 result_id 的待复核简答计数，消除逐 result 子查询（N+1）
    result_ids = [r.id for r in results]
    pending_map: dict[int, int] = {}
    if result_ids:
        pending_map = {
            r[0]: int(r[1]) for r in db.execute(
                select(ShortAnswerReview.exam_result_id, func.count(ShortAnswerReview.id))
                .where(
                    ShortAnswerReview.exam_result_id.in_(result_ids),
                    ShortAnswerReview.verdict.is_(None),
                ).group_by(ShortAnswerReview.exam_result_id)
            ).all()
        }
    published = 0
    published_session_ids: list[int] = []
    for res in results:
        # 含简答且仍有未复核 → 跳过
        if pending_map.get(res.id, 0) > 0:
            continue
        res.passed = res.score >= e.pass_score
        res.published = True
        published += 1
        if res.exam_session_id:
            published_session_ids.append(res.exam_session_id)
    # 仅更新已公布成绩对应的会话状态为 reviewed；保留进行中(in_progress)会话不被误改
    from app.models.record import ExamSession
    if published_session_ids:
        sessions = db.execute(
            select(ExamSession).where(ExamSession.id.in_(published_session_ids))
        ).scalars().all()
        for s in sessions:
            s.status = "reviewed"
    db.commit()
    return {"published": published}
