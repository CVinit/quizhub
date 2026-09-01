"""简答复核业务：待复核列表、逐题复核、公布成绩。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.models.exam import ExamDefinition
from app.models.question import Question
from app.models.record import ExamResult, ShortAnswerReview
from app.models.user import User


def list_pending(db: Session, scope: set[int] | None = None, limit: int = 500) -> list[dict]:
    """待复核简答列表。scope 非 None（部门管理员）时仅返回其数据范围内用户的待复核项。"""
    stmt = (
        select(ShortAnswerReview, ExamResult, ExamDefinition, Question, User)
        .join(ExamResult, ExamResult.id == ShortAnswerReview.exam_result_id)
        .join(ExamDefinition, ExamDefinition.id == ExamResult.exam_definition_id)
        .join(Question, Question.id == ShortAnswerReview.question_id)
        .join(User, User.id == ShortAnswerReview.user_id)
        .where(ShortAnswerReview.verdict.is_(None))
        .order_by(ShortAnswerReview.id.desc())
        .limit(limit)
    )
    if scope is not None:
        from app.core.deps import users_in_scope

        scoped_users = users_in_scope(db, scope)
        if not scoped_users:
            return []
        stmt = stmt.where(ShortAnswerReview.user_id.in_(scoped_users))
    rows = db.execute(stmt).all()
    out = []
    for r, _result, exam, question, user in rows:
        out.append(
            {
                "id": r.id,
                "exam_result_id": r.exam_result_id,
                "exam_name": exam.name,
                "user": user.email,
                "question_id": r.question_id,
                "question": question.question[:60],
                "user_answer": r.user_answer,
                "reference_answer": r.reference_answer,
            }
        )
    return out


def review(
    db: Session,
    review_id: int,
    verdict: str,
    partial_score: float | None,
    reviewer: User,
    scope: set[int] | None = None,
) -> dict:
    if verdict not in ("pass", "fail", "partial"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "verdict 必须是 pass/fail/partial")
    r = db.get(ShortAnswerReview, review_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "复核记录不存在")

    # 单题复核 IDOR 防护：review_id 可枚举，list_pending 的 scope 过滤不足以拦住直接
    # POST /admin/review/{id}。此处对归属考生做 user_in_scope 校验，部门管理员不得
    # 复核/改分其数据范围外的简答（与 list_pending/publish_results 的 scope 口径一致）。
    if scope is not None:
        from app.core.deps import user_in_scope

        if not user_in_scope(db, r.user_id, scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权复核该题")

    result = db.get(ExamResult, r.exam_result_id)
    if not result:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "成绩记录不存在")

    # 计算本题分值并校验 partial_score 上限（防超分）
    eqs = _get_exam_question_score(db, result.exam_definition_id, r.question_id)
    if eqs is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "复核题目不属于该考试")
    if verdict == "partial":
        if partial_score is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "partial 必须提供 partial_score")
        if partial_score < 0 or partial_score > eqs:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"partial_score 必须在 0~{eqs} 之间")

    # 原子条件占用复核权：仅当 verdict 仍为 NULL 时才能获得该题的复核权。
    # 这样两位复核人对同一 review_id 并发提交时，只有一方 rowcount=1 持有权，
    # 另一方 rowcount=0 → 400，杜绝非原子读-判-写导致的分数重复自增。
    claimed = cast(
        CursorResult,
        db.execute(
            update(ShortAnswerReview)
            .where(ShortAnswerReview.id == review_id, ShortAnswerReview.verdict.is_(None))
            .values(
                verdict=verdict,
                partial_score=partial_score if verdict == "partial" else None,
                reviewer=reviewer.id,
                reviewed_at=datetime.now(timezone.utc).isoformat(),
            )
        ),
    )
    if claimed.rowcount == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该题已复核")

    # 原子自增成绩，避免并发复核读-改-写丢失更新

    if verdict == "pass":
        db.execute(update(ExamResult).where(ExamResult.id == result.id).values(score=ExamResult.score + eqs))
    elif verdict == "partial":
        db.execute(update(ExamResult).where(ExamResult.id == result.id).values(score=ExamResult.score + partial_score))
    db.commit()
    return {"success": True, "verdict": verdict}


def _get_exam_question_score(db: Session, exam_id: int, qid: int) -> float | None:
    from app.models.exam import ExamQuestion

    eq = db.execute(
        select(ExamQuestion).where(ExamQuestion.exam_definition_id == exam_id, ExamQuestion.question_id == qid)
    ).scalar_one_or_none()
    return eq.score if eq else None


def publish_results(db: Session, exam_id: int, scope: set[int] | None = None, bg=None) -> dict:
    """公布某考试所有成绩（要求该考试所有简答已复核）。

    同时处理「无简答但 show_score_immediately=False」的成绩公布场景：
    这类成绩在交卷时 published=False，需经此接口由管理员手动公布。
    scope 非 None（部门管理员）时先校验考试指派分组落在其范围内。
    """
    from app.models.exam import ExamDefinition

    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试不存在")
    # 部门管理员数据范围校验：考试指派分组须与其子树有交集
    if scope is not None:
        e_groups = e.group_ids or []
        if not e_groups or not set(e_groups).issubset(scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权操作该考试")
    results = (
        db.execute(
            select(ExamResult).where(ExamResult.exam_definition_id == exam_id, ExamResult.published == False)  # noqa: E712
        )
        .scalars()
        .all()
    )
    # 一次查询所有 result_id 的待复核简答计数，消除逐 result 子查询（N+1）
    result_ids = [r.id for r in results]
    pending_map: dict[int, int] = {}
    if result_ids:
        pending_map = {
            r[0]: int(r[1])
            for r in db.execute(
                select(ShortAnswerReview.exam_result_id, func.count(ShortAnswerReview.id))
                .where(
                    ShortAnswerReview.exam_result_id.in_(result_ids),
                    ShortAnswerReview.verdict.is_(None),
                )
                .group_by(ShortAnswerReview.exam_result_id)
            ).all()
        }
    published = 0
    published_session_ids: list[int] = []
    for res in results:
        # 含简答且仍有未复核 → 跳过
        if pending_map.get(res.id, 0) > 0:
            continue
        # 保持"超时即不及格"语义：超时交卷（res.overtime=True）即便复核给分后总分过线也不判及格
        res.passed = (not res.overtime) and (res.score >= e.pass_score)
        res.published = True
        published += 1
        if res.exam_session_id:
            published_session_ids.append(res.exam_session_id)
    # 仅更新已公布成绩对应的会话状态为 reviewed；保留进行中(in_progress)会话不被误改
    from app.models.record import ExamSession

    if published_session_ids:
        sessions = db.execute(select(ExamSession).where(ExamSession.id.in_(published_session_ids))).scalars().all()
        for s in sessions:
            s.status = "reviewed"
    db.commit()
    if bg is not None and published:
        from app.services import mail_service
        from app.services.system_service import get_settings

        recipient_ids = [res.user_id for res in results if res.published]
        recipients = db.execute(select(User).where(User.id.in_(recipient_ids))).scalars().all()
        recipient_map = {recipient.id: recipient for recipient in recipients}
        settings = get_settings(db)
        for res in results:
            recipient = recipient_map.get(res.user_id)
            if res.published and recipient:
                bg.add_task(mail_service.send_review_done, settings, recipient.email, e.name, res.score)
    return {"published": published}
