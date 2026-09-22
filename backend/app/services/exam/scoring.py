"""作答与结算：逐题乐观锁落库、交卷判分、成绩读取、卡死会话回收。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import cast

from fastapi import status
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.question import Question
from app.models.record import ExamResult, ExamSession, ShortAnswerReview
from app.models.user import User
from app.services.exam.common import _is_overtime, _now, _parse_time
from app.services.grading import grade, is_passed

logger = logging.getLogger("quizhub")


# ---------- 逐题作答（原子乐观锁）----------
def submit_answer(db: Session, user: User, session_id: int, qid: int, answer, version: int) -> dict:
    """原子条件更新 answers + version，避免并发覆盖（原实现为非原子 SELECT-check-UPDATE）。

    原子 UPDATE ... WHERE id=? AND version=? AND status='in_progress' 形如架构决策：
    affected=0 → 409。status 条件防止 submit_exam 已置 scoring 后，滞后的 submit_answer
    仍按旧 version 写入并污染已结算会话的 answers。
    """
    sess = db.get(ExamSession, session_id)
    if not sess or sess.user_id != user.id:
        raise DomainError(status.HTTP_404_NOT_FOUND, "考试会话不存在")
    if sess.status != "in_progress":
        raise DomainError(status.HTTP_400_BAD_REQUEST, "考试已结束，无法作答")
    e = db.get(ExamDefinition, sess.exam_definition_id)
    if not e:
        raise DomainError(status.HTTP_404_NOT_FOUND, "考试定义不存在")
    if _is_overtime(e, sess):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "考试已超时，请提交试卷")
    if not db.execute(
        select(ExamQuestion.id).where(
            ExamQuestion.exam_definition_id == sess.exam_definition_id,
            ExamQuestion.question_id == qid,
        )
    ).first():
        raise DomainError(status.HTTP_400_BAD_REQUEST, "题目不属于当前考试")

    # 合并答案（基于本次读取的 answers 快照）
    answers = dict(sess.answers or {})
    answers[str(qid)] = {"answer": answer, "answered_at": _now()}
    new_version = version + 1

    # 原子条件更新：仅当数据库当前 version == 期望 version 且会话仍在进行中时才写入
    result = cast(
        CursorResult,
        db.execute(
            update(ExamSession)
            .where(ExamSession.id == session_id, ExamSession.version == version, ExamSession.status == "in_progress")
            .values(answers=answers, version=new_version)
        ),
    )
    if result.rowcount == 0:
        # version 已被他人改动 或 会话已结束 → 冲突
        raise DomainError(status.HTTP_409_CONFLICT, "数据版本冲突，请刷新后重试")

    db.commit()
    return {"version": new_version}


# ---------- 交卷结算 ----------
# scoring 状态超过该秒数视为崩溃残留，可被回收（启动刷新时或下次开考时）
_SCORING_TIMEOUT_SEC = 30 * 60


def _recover_stuck_scoring(db: Session, user_id: int | None = None) -> int:
    """回收超时的 scoring 会话：将其重置回 in_progress，释放被卡死的尝试次数。

    仅重置「无 ExamResult 且超时」的会话：含 ExamResult 的 scoring 会话是合法
    等待简答复核状态（submit_exam 已写 result 但会话保持 scoring），绝不可被回收，
    否则会让考生重新作答、改答案，污染复核流程。仅在单进程串行写的前提下安全（SQLite WAL）。

    Args:
        db: 数据库会话。
        user_id: 仅回收该用户的会话；None 表示全量回收（仅限启动维护路径调用，
            用户可达的开考路径必须传入本人 id，避免一个用户改写他人的会话状态）。

    Returns:
        被回收的会话数量。
    """
    # 用 aware datetime 比较：旧实现把 ISO 字符串在本地时区下取 timestamp()，
    # 无时区后缀的历史数据会被整体前移（UTC+8 下偏移 8 小时），
    # 使已结束的会话被误判为超时并复活，考生可重新作答绕过 max_attempts。
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_SCORING_TIMEOUT_SEC)
    stmt = select(ExamSession).where(ExamSession.status == "scoring")
    if user_id is not None:
        stmt = stmt.where(ExamSession.user_id == user_id)
    rows = db.execute(stmt).scalars().all()
    if not rows:
        return 0

    # 一次查出所有 session 是否已有成绩，消除逐行查询（N+1）
    with_result = {
        r[0]
        for r in db.execute(
            select(ExamResult.exam_session_id).where(ExamResult.exam_session_id.in_([s.id for s in rows]))
        ).all()
    }

    recovered = 0
    for s in rows:
        try:
            submitted = _parse_time(s.submitted_at or s.started_at)
        except DomainError:
            # 时间字段无法解析：保守跳过，交由人工核查，绝不复活会话
            continue
        if submitted < cutoff and s.id not in with_result:
            s.status = "in_progress"
            recovered += 1
    if recovered:
        db.commit()
    return recovered


def submit_exam(db: Session, user: User, session_id: int) -> dict:
    sess = db.get(ExamSession, session_id)
    if not sess or sess.user_id != user.id:
        raise DomainError(status.HTTP_404_NOT_FOUND, "考试会话不存在")

    e = db.get(ExamDefinition, sess.exam_definition_id)
    if not e:
        raise DomainError(status.HTTP_404_NOT_FOUND, "考试定义不存在")

    # 正式考试截止时间校验：超过 end_at 仍允许交卷但判为超时（成绩置 0），
    # 避免仅靠前端计时、用户越过截止时间仍正常得分。
    overtime = _is_overtime(e, sess)

    # 幂等保护：原子把状态置为 scoring，若已非 in_progress 则 rowcount=0，避免重复提交
    locked = cast(
        CursorResult,
        db.execute(
            update(ExamSession)
            .where(ExamSession.id == session_id, ExamSession.status == "in_progress")
            .values(status="scoring", submitted_at=_now())
        ),
    )
    if locked.rowcount == 0:
        # 已被并发提交或已交卷：返回已有结果（幂等），而非报错
        existing = db.execute(select(ExamResult).where(ExamResult.exam_session_id == session_id)).scalar_one_or_none()
        if existing and existing.published:
            return {
                "need_review": False,
                "score": existing.score,
                "total_score": existing.total_score,
                "passed": existing.passed,
                "correct_count": existing.correct_count,
                "total_count": existing.total_count,
                "overtime": existing.overtime,
            }
        if existing and not existing.published:
            return {"need_review": True, "message": "含简答题，待管理员复核后公布成绩", "overtime": existing.overtime}
        raise DomainError(status.HTTP_400_BAD_REQUEST, "考试已结束")

    # 锁定成功后，answers 必须以数据库当前值为准：本函数入口的 db.get(ExamSession) 读到的是
    # 进入函数时的快照，而在「读快照 → 滞后的 submit_answer 提交并 commit → 本请求锁定 scoring」
    # 这一窗口下，sess.answers 仍是旧内存快照，直接用它判分会丢失滞后提交的作答。锁定的 Core UPDATE
    # 不刷新 ORM 对象，故此处显式 refresh 重新读取（含任何并发已 commit 的 answers）。
    db.refresh(sess)

    eqs = db.execute(select(ExamQuestion).where(ExamQuestion.exam_definition_id == e.id)).scalars().all()

    # 批量取题目，消除 N+1
    qids = [eq.question_id for eq in eqs]
    q_map: dict[int, Question] = {}
    if qids:
        for question in db.execute(select(Question).where(Question.id.in_(qids))).scalars().all():
            q_map[question.id] = question

    correct_count = 0
    total_count = len(eqs)
    objective_score = 0.0
    total_score = sum(eq.score for eq in eqs) if eqs else 0
    # `ExamDefinition.need_review`（管理端「需要复核」开关）此前只写不读：管理员显式勾选
    # 要求复核、但组卷恰好没抽到简答题时，成绩会立即公布，与该开关的语义相悖。
    need_review = bool(e.need_review)
    short_answers: list[tuple[int, str, str]] = []

    answers = sess.answers or {}
    # 超时则客观题不计分，但仍记录作答并走复核流程（若有简答）
    for eq in eqs:
        q: Question | None = q_map.get(eq.question_id)
        if not q:
            continue
        user_ans = answers.get(str(q.id), {}).get("answer")
        if q.type == "简答题":
            need_review = True
            short_answers.append((q.id, str(user_ans or ""), str(q.answer or "")))
        else:
            ok = grade(q.type, q.answer, user_ans)
            if ok and not overtime:
                correct_count += 1
                objective_score += eq.score

    # 计算客观题得分（简答部分待复核后加）
    score = objective_score
    # 及格判定统一走百分制（pass_score 是百分制，score/total_score 是原始分）：
    # 直接比较 raw score 会让 10/20/30 题的卷子（满分 20/40/60）永远不及格。
    passed = (not need_review) and is_passed(score, total_score, e.pass_score, overtime=overtime)

    result = ExamResult(
        exam_definition_id=e.id,
        user_id=user.id,
        exam_session_id=sess.id,
        score=score,
        total_score=total_score,
        passed=passed,
        correct_count=correct_count,
        total_count=total_count,
        objective_score=objective_score,
        need_review=need_review,
        published=(not need_review and e.show_score_immediately and not overtime),
        overtime=overtime,
    )
    db.add(result)
    db.flush()  # 拿到 result.id 再写简答复核记录的外键

    # 生成简答复核记录（同事务，单次 commit）
    for qid, ua, ref in short_answers:
        db.add(
            ShortAnswerReview(
                exam_result_id=result.id,
                exam_session_id=sess.id,
                user_id=user.id,
                question_id=qid,
                user_answer=ua,
                reference_answer=ref,
            )
        )

    # 更新会话状态（单次 commit 收尾）
    sess.status = "scored" if not need_review else "scoring"
    db.commit()
    db.refresh(result)

    # 交卷生成成绩会影响当日统计（考试数/分数），即时刷新该考生当日聚合。
    # 成绩已提交且本接口幂等，统计属于派生数据：刷新失败不应把「已交卷」变成 500，
    # 否则前端误报失败、学生重复交卷（虽幂等但体验差）。下次开考/手动刷新会兜底重算。
    try:
        from app.services import stats_service

        stats_service.refresh_user_for_timestamps(db, user.id, [result.created_at])
    except Exception as exc:  # noqa: BLE001  派生统计失败不阻断交卷结果
        db.rollback()
        logger.warning("[exam] 交卷后统计刷新失败，已忽略：%s", type(exc).__name__)

    if need_review:
        return {"need_review": True, "message": "含简答题，待管理员复核后公布成绩", "overtime": overtime}
    return {
        "need_review": False,
        "score": score,
        "total_score": total_score,
        "passed": passed,
        "correct_count": correct_count,
        "total_count": total_count,
        "show_analysis": e.show_analysis,
        "overtime": overtime,
    }


def get_result(db: Session, user: User, session_id: int) -> dict:
    sess = db.get(ExamSession, session_id)
    if not sess or sess.user_id != user.id:
        raise DomainError(status.HTTP_404_NOT_FOUND, "考试会话不存在")
    result = db.execute(select(ExamResult).where(ExamResult.exam_session_id == session_id)).scalar_one_or_none()
    if not result:
        return {"published": False, "message": "成绩尚未生成"}
    if not result.published:
        # 区分未公布的原因：原实现把「超时」「待管理员公布」也一律说成「待复核后公布」，
        # 而这些成绩根本没有复核项，考生/管理员都会误解为"还卡在复核"。
        if result.need_review:
            return {"published": False, "need_review": True, "message": "含简答题，待复核后公布"}
        if result.overtime:
            return {"published": False, "overtime": True, "message": "本次考试超时，成绩作废（不计分）"}
        return {"published": False, "message": "成绩待管理员公布"}
    return {
        "published": True,
        "score": result.score,
        "total_score": result.total_score,
        "passed": result.passed,
        "correct_count": result.correct_count,
        "total_count": result.total_count,
    }
