"""简答复核业务：待复核列表、逐题复核、公布成绩。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from fastapi import status
from sqlalchemy import and_, case, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.question import Question
from app.models.record import ExamResult, ShortAnswerReview
from app.models.user import User


def list_pending(
    db: Session, scope: set[int] | None = None, limit: int = 500, verdict: str | None = None
) -> list[dict]:
    """简答复核列表。scope 非 None（部门管理员）时仅返回其数据范围内用户的复核项。

    verdict 为状态筛选：
    - None / "pending"：待复核（verdict IS NULL），保持原有默认行为；
    - "pass" / "fail" / "partial"：按已复核结论筛选；
    - "done"：所有已复核项（任一 verdict 非空）。
    """
    stmt = (
        select(ShortAnswerReview, ExamResult, ExamDefinition, Question, User, ExamQuestion)
        .join(ExamResult, ExamResult.id == ShortAnswerReview.exam_result_id)
        .join(ExamDefinition, ExamDefinition.id == ExamResult.exam_definition_id)
        .join(Question, Question.id == ShortAnswerReview.question_id)
        .join(User, User.id == ShortAnswerReview.user_id)
        # 取该题在本次考试中的分值：前端「部分得分」上限必须是它，硬编码会超分或给不满分。
        # 用外连接：题目若已从考试卷面移除，也不能把这条待复核记录从列表里丢掉。
        .outerjoin(
            ExamQuestion,
            and_(
                ExamQuestion.exam_definition_id == ExamResult.exam_definition_id,
                ExamQuestion.question_id == ShortAnswerReview.question_id,
            ),
        )
        .order_by(ShortAnswerReview.id.desc())
        .limit(limit)
    )
    if verdict in (None, "pending"):
        stmt = stmt.where(ShortAnswerReview.verdict.is_(None))
    elif verdict == "done":
        stmt = stmt.where(ShortAnswerReview.verdict.is_not(None))
    else:
        stmt = stmt.where(ShortAnswerReview.verdict == verdict)
    if scope is not None:
        from app.core.deps import user_ids_subquery

        stmt = stmt.where(ShortAnswerReview.user_id.in_(user_ids_subquery(scope)))
    rows = db.execute(stmt).all()
    out = []
    for r, _result, exam, question, user, exam_question in rows:
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
                "score": exam_question.score if exam_question is not None else question.score,
                # 复核状态：前端据此区分「待复核」与「已复核」行（已复核只读展示结论）
                "verdict": r.verdict,
                "partial_score": r.partial_score,
                "reviewed_at": r.reviewed_at,
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
        raise DomainError(status.HTTP_400_BAD_REQUEST, "verdict 必须是 pass/fail/partial")
    r = db.get(ShortAnswerReview, review_id)
    if not r:
        raise DomainError(status.HTTP_404_NOT_FOUND, "复核记录不存在")

    # 单题复核 IDOR 防护：review_id 可枚举，list_pending 的 scope 过滤不足以拦住直接
    # POST /admin/review/{id}。此处对归属考生做 user_in_scope 校验，部门管理员不得
    # 复核/改分其数据范围外的简答（与 list_pending/publish_results 的 scope 口径一致）。
    if scope is not None:
        from app.core.deps import user_in_scope

        if not user_in_scope(db, r.user_id, scope):
            raise DomainError(status.HTTP_403_FORBIDDEN, "无权复核该题")

    result = db.get(ExamResult, r.exam_result_id)
    if not result:
        raise DomainError(status.HTTP_404_NOT_FOUND, "成绩记录不存在")

    # 计算本题分值并校验 partial_score 上限（防超分）
    eqs = _get_exam_question_score(db, result.exam_definition_id, r.question_id)
    if eqs is None:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "复核题目不属于该考试")
    # 增量必须按 verdict 显式取值（fail 的得分是 0.0，而不是 None）。
    # 原实现 `delta = eqs if verdict == "pass" else partial_score` 在 fail 时
    # partial_score 为 None，使 SQL 中 `score + NULL` 与标量 `min(total_score, NULL)`
    # 均得 NULL，写入 NOT NULL 的 exam_results.score 直接 IntegrityError → 500，
    # 导致「简答判不通过」这一核心动作完全不可用。
    delta: float
    if verdict == "pass":
        delta = float(eqs)
    elif verdict == "partial":
        if partial_score is None:
            raise DomainError(status.HTTP_400_BAD_REQUEST, "partial 必须提供 partial_score")
        if partial_score < 0 or partial_score > eqs:
            raise DomainError(status.HTTP_400_BAD_REQUEST, f"partial_score 必须在 0~{eqs} 之间")
        delta = float(partial_score)
    else:  # fail
        delta = 0.0

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
        raise DomainError(status.HTTP_400_BAD_REQUEST, "该题已复核")

    # 原子自增成绩，避免并发复核读-改-写丢失更新。
    # 必须同时满足两点，否则成绩会失真：
    # 1) 以 total_score 封顶：多次复核不同题目累加可能超过满分（如 108/100）；
    # 2) 同步 objective_score：该列是 score 的组成部分，只加 score 会让
    #    任何按 objective_score 重算的报告与 score 永久不一致。
    db.execute(
        update(ExamResult)
        .where(ExamResult.id == result.id)
        .values(
            score=func.min(ExamResult.total_score, ExamResult.score + delta),
            objective_score=ExamResult.objective_score + delta,
        )
    )
    db.commit()
    # 复核改分会影响该考生当日成绩聚合，即时刷新其当日行
    from app.services import stats_service

    stats_service.refresh_user_for_timestamps(db, r.user_id, [result.created_at])
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
        raise DomainError(status.HTTP_404_NOT_FOUND, "考试不存在")
    # 部门管理员数据范围校验：考试的全部分组都须落在其子树内（子集口径，
    # 与 exam/admin._check_exam_scope 保持一致）
    if scope is not None:
        e_groups = e.group_ids or []
        if not e_groups or not set(e_groups).issubset(scope):
            raise DomainError(status.HTTP_403_FORBIDDEN, "无权操作该考试")

    # 只取关键列，避免把每条成绩实例化为 ORM 对象后逐行 flush（写事务时间随人数线性增长）
    stmt = select(ExamResult.id, ExamResult.user_id, ExamResult.exam_session_id, ExamResult.created_at).where(
        ExamResult.exam_definition_id == exam_id, ExamResult.published.is_(False)
    )
    if scope is not None:
        # 成绩归属考生同样必须在调用者数据范围内：考试分组校验只约束了「这一场考试」，
        # 考生可能在交卷后被移出部门。与 exam/admin.py:list_results 的过滤口径保持一致，
        # 否则部门管理员能公布并邮件通知范围外考生的成绩。
        from app.core.deps import user_ids_subquery

        stmt = stmt.where(ExamResult.user_id.in_(user_ids_subquery(scope)))
    rows = db.execute(stmt).all()
    if not rows:
        return {"published": 0}
    # 一次查询所有 result_id 的待复核简答，跳过仍有未复核的
    result_ids = [r[0] for r in rows]
    pending_ids = {
        r[0]
        for r in db.execute(
            select(ShortAnswerReview.exam_result_id)
            .where(
                ShortAnswerReview.exam_result_id.in_(result_ids),
                ShortAnswerReview.verdict.is_(None),
            )
            .distinct()
        ).all()
    }
    eligible = [r for r in rows if r[0] not in pending_ids]
    if not eligible:
        return {"published": 0}
    meta = {r[0]: (r[1], r[2]) for r in eligible}

    # 单条条件 UPDATE + RETURNING 占用：并发发布时后到者匹配到 0 行，
    # 不会重复计数，也不会给同一考生重复发「成绩已公布」邮件。
    # 保持"超时即不及格"语义：overtime=True 即便复核给分后过线也不判及格。
    claimed = db.execute(
        update(ExamResult)
        .where(ExamResult.id.in_(list(meta)), ExamResult.published.is_(False))
        .values(
            published=True,
            # 及格线是百分制、成绩是原始分，必须先归一化再比较（与 grading.is_passed 同口径）。
            # total_score=0 时 SQLite 除法得 NULL，比较结果为 NULL，自然落到 else_=False。
            # round(...,6) 与 is_passed 的容差一致：否则数学上刚好压线的成绩会在
            # 「手动公布」与「Python 判分」两条路径上得出不同结论。
            passed=case(
                (
                    and_(
                        ExamResult.overtime.is_(False),
                        ExamResult.total_score > 0,
                        func.round(ExamResult.score * 100.0 / ExamResult.total_score, 6) >= e.pass_score,
                    ),
                    True,
                ),
                else_=False,
            ),
        )
        .returning(ExamResult.id, ExamResult.user_id, ExamResult.score, ExamResult.created_at)
    ).all()
    if not claimed:
        return {"published": 0}

    from app.models.record import ExamSession

    claimed_ids = [r[0] for r in claimed]
    reviewed_session_ids = [meta[cid][1] for cid in claimed_ids if meta[cid][1]]
    if reviewed_session_ids:
        # 仅更新已公布成绩对应的会话；保留进行中(in_progress)会话不被误改
        db.execute(update(ExamSession).where(ExamSession.id.in_(reviewed_session_ids)).values(status="reviewed"))
    db.commit()

    # 成绩由「未公布」变为「已公布」会改变统计口径（聚合只纳入已公布正式成绩），
    # 因此必须在提交后重算受影响日期的聚合，否则排行榜要等到下次日刷/手动刷新才更新。
    from app.services import stats_service

    stats_service.refresh_for_timestamps(db, [r[3] for r in claimed])

    if bg is not None:
        from app.services import mail_service
        from app.services.system_service import get_settings

        recipient_ids = [r[1] for r in claimed]
        recipients = db.execute(select(User).where(User.id.in_(recipient_ids))).scalars().all()
        recipient_map = {recipient.id: recipient for recipient in recipients}
        settings = get_settings(db)
        # 收敛为一个后台任务：逐人挂任务会让任务对象随考生数线性增长
        # （与 exam/admin.publish_exam 的批量通知同口径）。
        mail_jobs = [
            (recipient_map[user_id].email, score)
            for _result_id, user_id, score, _created_at in claimed
            if user_id in recipient_map
        ]
        if mail_jobs:
            bg.add_task(
                mail_service.send_safely,
                mail_service.send_review_done_many,
                settings,
                mail_jobs,
                e.name,
            )
    return {"published": len(claimed)}
