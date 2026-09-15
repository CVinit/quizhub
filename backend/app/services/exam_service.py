"""考试业务：会话、逐题落库（乐观锁）、结算、模拟/正式考试。

- 模拟考试：套用默认规则（mock-config），用户自助开考。
- 正式考试：管理员发布指派分组，单场规则。
- 简答需复核时 published=false，复核完成后公布。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast

from fastapi import HTTPException, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.exam import ExamDefinition, ExamQuestion, PaperTemplate
from app.models.group import Group, UserGroup
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult, ExamSession, ShortAnswerReview
from app.models.user import User
from app.services import mail_service
from app.services.grading import grade
from app.services.paper_service import generate_paper
from app.services.system_service import get_settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_can_access_exam(e: ExamDefinition, user_group_ids: set[int]) -> bool:
    """考试指派分组与用户分组有交集（空指派表示不限）。"""
    e_groups = e.group_ids or []
    return not e_groups or bool(set(e_groups).intersection(user_group_ids))


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "考试时间配置无效") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _within_time_window(e: ExamDefinition, now: datetime) -> tuple[bool, str]:
    """正式考试时段校验。返回 (是否在窗口内, 状态)。"""
    if e.type != "formal":
        return True, ""
    if e.start_at and now < _parse_time(e.start_at):
        return False, "not_started"
    if e.end_at and now >= _parse_time(e.end_at):
        return False, "ended"
    return True, ""


def _is_overtime(e: ExamDefinition, sess: ExamSession, now: datetime | None = None) -> bool:
    """按服务端开始时间和考试截止时间判断是否超时。"""
    now = now or datetime.now(timezone.utc)
    deadline = _parse_time(sess.started_at) + timedelta(minutes=e.duration_min)
    if e.end_at:
        deadline = min(deadline, _parse_time(e.end_at))
    return now >= deadline


# ---------- 用户端：可用考试 ----------
def list_available(db: Session, user: User) -> list[dict]:
    """返回当前用户可参加的考试。

    仅返回正式考试（type=formal）；模拟考试（type=mock）为用户自助发起，不经此列表，
    避免用户 A 的模拟考出现在用户 B 的可用列表中污染数据。
    """
    from app.models.group import UserGroup

    user_group_ids = {r[0] for r in db.execute(select(UserGroup.group_id).where(UserGroup.user_id == user.id)).all()}
    if user.dept_group_id:
        user_group_ids.add(user.dept_group_id)

    now = datetime.now(timezone.utc)
    rows = (
        db.execute(
            select(ExamDefinition)
            .where(
                ExamDefinition.status.in_(["published", "ongoing"]),
                ExamDefinition.type == "formal",  # 仅正式考试进入可用列表
            )
            .order_by(ExamDefinition.id.desc())
        )
        .scalars()
        .all()
    )

    out = []
    for e in rows:
        if not _user_can_access_exam(e, user_group_ids):
            continue
        ok, state = _within_time_window(e, now)
        if not ok:
            out.append(_exam_brief(e, state))
            continue
        # 尝试次数
        attempts = _count_attempts(db, e.id, user.id)
        if e.max_attempts and attempts >= e.max_attempts:
            out.append(_exam_brief(e, "max_reached"))
            continue
        out.append(_exam_brief(e, "available", attempts))
    return out


def _count_attempts(db: Session, exam_id: int, user_id: int) -> int:
    # scoring 视为结算中、不确定是否计入尝试次数；仅计已确认结束的终态，
    # 避免崩溃残留的 scoring 会话把用户尝试次数永久占满。
    return len(
        db.execute(
            select(ExamSession.id).where(
                ExamSession.exam_definition_id == exam_id,
                ExamSession.user_id == user_id,
                ExamSession.status.in_(["submitted", "scored", "reviewed"]),
            )
        ).all()
    )


def _exam_brief(e: ExamDefinition, state: str, attempts: int = 0) -> dict:
    """用户端考试摘要。

    刻意不包含 rules / paper_template_id / group_ids：这些是组卷配置与指派范围，
    属于管理端信息，不应暴露给参加考试的用户。
    """
    return {
        "id": e.id,
        "name": e.name,
        "type": e.type,
        "status": e.status,
        "start_at": e.start_at,
        "end_at": e.end_at,
        "duration_min": e.duration_min,
        "pass_score": e.pass_score,
        "state": state,
        "attempts": attempts,
        "max_attempts": e.max_attempts,
        "total_questions": _exam_question_count(e),
    }


def _exam_admin_brief(e: ExamDefinition) -> dict:
    """管理端考试列表项：在用户端摘要基础上补齐可编辑配置。

    编辑弹窗需要回填组卷方式、题型配比、来源题库与指派分组；缺失这些字段会导致
    打开编辑时配比/题库显示为空，保存后原配置被清空。
    """
    brief = _exam_brief(e, e.status)
    brief.update(
        {
            "rules": e.rules or {},
            "paper_template_id": e.paper_template_id,
            "manual_questions": e.manual_questions,
            "group_ids": e.group_ids or [],
            "need_review": e.need_review,
            "show_score_immediately": e.show_score_immediately,
            "show_analysis": e.show_analysis,
        }
    )
    return brief


def _exam_question_count(e: ExamDefinition) -> int:
    if e.manual_questions:
        return len(e.manual_questions)
    # 实际题目数在固化时确定，这里返回 rules 中的配额和（近似）
    rules = e.rules or {}
    quota = rules.get("type_quota") or {}
    return sum(quota.values())


# ---------- 开考 ----------
def start_exam(db: Session, user: User, exam_id: int) -> dict:
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试不存在")
    if e.status not in ("published", "ongoing"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "考试未开放")
    # 权限：正式考试须在指派分组内且在时段窗内（start_exam 与 list_available 复用同一校验，
    # 防止用户猜枚举 exam_id 开考未指派/未到时段的考试）
    if e.type == "formal":
        from app.models.group import UserGroup

        user_group_ids = {
            r[0] for r in db.execute(select(UserGroup.group_id).where(UserGroup.user_id == user.id)).all()
        }
        if user.dept_group_id:
            user_group_ids.add(user.dept_group_id)
        if not _user_can_access_exam(e, user_group_ids):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "您不在该考试指派范围内")
        ok, _state = _within_time_window(e, datetime.now(timezone.utc))
        if not ok:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "考试不在开放时段")
    # 是否有进行中的会话
    ongoing = db.execute(
        select(ExamSession).where(
            ExamSession.exam_definition_id == exam_id,
            ExamSession.user_id == user.id,
            ExamSession.status.in_(["in_progress", "scoring"]),
        )
    ).scalar_one_or_none()
    if ongoing:
        # scoring 但无对应成绩记录 → 进程崩溃残留，回收为可续答
        if ongoing.status == "scoring":
            has_result = db.execute(select(ExamResult.id).where(ExamResult.exam_session_id == ongoing.id)).first()
            if not has_result:
                _recover_stuck_scoring(db, user_id=user.id)
                ongoing.status = "in_progress"
                db.commit()
                db.refresh(ongoing)
        return _session_payload(db, ongoing, e)

    # 未结束的会话也算占用次数
    attempts = _count_attempts(db, exam_id, user.id)
    if e.max_attempts and attempts >= e.max_attempts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "已达最大尝试次数")

    # 固化题目（若尚未固化）
    _ensure_exam_questions(db, e)

    # 创建会话
    sess = ExamSession(
        exam_definition_id=exam_id,
        user_id=user.id,
        status="in_progress",
        answers={},
        version=1,
        started_at=_now(),
        submitted_at=None,
        remaining_sec=e.duration_min * 60,
    )
    db.add(sess)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        ongoing = db.execute(
            select(ExamSession).where(
                ExamSession.exam_definition_id == exam_id,
                ExamSession.user_id == user.id,
                ExamSession.status.in_(["in_progress", "scoring"]),
            )
        ).scalar_one_or_none()
        if ongoing:
            return _session_payload(db, ongoing, e)
        raise
    db.refresh(sess)
    return _session_payload(db, sess, e)


def _ensure_exam_questions(db: Session, e: ExamDefinition) -> list[int]:
    """确保 exam_questions 已固化，返回题目 id 列表。

    固化本身用单条 commit 收尾；并发首次固化由 (exam_definition_id, question_id)
    唯一约束兜底：后到请求若已存在则直接读取，不重复插入。
    """
    existing = db.execute(select(ExamQuestion).where(ExamQuestion.exam_definition_id == e.id)).scalars().all()
    if existing:
        return sorted({eq.question_id for eq in existing})

    # 确定题目来源
    if e.manual_questions:
        qids = list(e.manual_questions)
        if len(qids) != len(set(qids)):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "同一考试不能重复添加题目")
    elif e.paper_template_id:
        tpl = db.get(PaperTemplate, e.paper_template_id)
        if tpl and tpl.question_ids:
            qids = list(tpl.question_ids)
        else:
            # 用模板规则即时生成
            paper = generate_paper(db, tpl.config if tpl else (e.rules or {}))
            qids = paper["question_ids"]
            scores = paper["scores"]
            _persist_exam_questions(db, e.id, qids, scores)
            return sorted(set(qids))
    else:
        # 用考试 rules 即时生成
        paper = generate_paper(db, e.rules or {})
        qids = paper["question_ids"]
        scores = paper["scores"]
        _persist_exam_questions(db, e.id, qids, scores)
        return sorted(set(qids))

    _persist_exam_questions(db, e.id, qids, None)
    return sorted(set(qids))


def _persist_exam_questions(db: Session, exam_id: int, qids: list[int], scores: dict | None) -> None:
    """固化题目。并发安全：插入前再次确认无已固化行（唯一约束兜底），逐 seq 构造后单次提交。

    不再在被调函数内提前 commit 破坏调用方事务边界：本函数仅在「首次固化」分支调用，
    调用方（start_exam）已无未提交写，故此处 commit 是安全的收尾点。
    """
    if len(qids) != len(set(qids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "同一考试不能重复添加题目")
    existing_questions = {row[0] for row in db.execute(select(Question.id).where(Question.id.in_(qids))).all()}
    missing = set(qids) - existing_questions
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "考试包含不存在的题目")
    # 并发兜底：唯一约束 + 插入前复核，避免双请求都读到 existing=[] 后重复插入
    already = db.execute(select(ExamQuestion.id).where(ExamQuestion.exam_definition_id == exam_id).limit(1)).first()
    if already:
        return
    pending = []
    for seq, qid in enumerate(qids):
        score = (scores or {}).get(qid, 2)
        pending.append(
            ExamQuestion(exam_definition_id=exam_id, question_id=qid, seq=seq, score=score, shuffle_map=None)
        )
    if pending:
        try:
            db.add_all(pending)
            db.commit()
        except IntegrityError:
            # 并发首次固化，他方已插入：回滚本事务内可能的写，交由后续读取已有行
            db.rollback()
    db.expire_all()


def _session_payload(db: Session, sess: ExamSession, e: ExamDefinition) -> dict:
    eqs = (
        db.execute(select(ExamQuestion).where(ExamQuestion.exam_definition_id == e.id).order_by(ExamQuestion.seq))
        .scalars()
        .all()
    )
    # 批量取题目，消除 N+1
    qids = [eq.question_id for eq in eqs]
    q_map: dict[int, Question] = {}
    if qids:
        for question in db.execute(select(Question).where(Question.id.in_(qids))).scalars().all():
            q_map[question.id] = question
    questions = []
    for eq in eqs:
        q: Question | None = q_map.get(eq.question_id)
        if q:
            questions.append(
                {
                    "seq": eq.seq,
                    "id": q.id,
                    "type": q.type,
                    "question": q.question,
                    "options": q.options,
                    "left_items": q.left_items,
                    "right_items": q.right_items,
                    "score": eq.score,
                    # 考试中不返回答案/解析
                }
            )
    return {
        "session_id": sess.id,
        "version": sess.version,
        "answers": sess.answers,
        "remaining_sec": sess.remaining_sec,
        "duration_min": e.duration_min,
        "started_at": sess.started_at,
        "questions": questions,
        "exam_name": e.name,
    }


# ---------- 逐题作答（原子乐观锁）----------
def submit_answer(db: Session, user: User, session_id: int, qid: int, answer, version: int) -> dict:
    """原子条件更新 answers + version，避免并发覆盖（原实现为非原子 SELECT-check-UPDATE）。

    原子 UPDATE ... WHERE id=? AND version=? AND status='in_progress' 形如架构决策：
    affected=0 → 409。status 条件防止 submit_exam 已置 scoring 后，滞后的 submit_answer
    仍按旧 version 写入并污染已结算会话的 answers。
    """
    sess = db.get(ExamSession, session_id)
    if not sess or sess.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试会话不存在")
    if sess.status != "in_progress":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "考试已结束，无法作答")
    e = db.get(ExamDefinition, sess.exam_definition_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试定义不存在")
    if _is_overtime(e, sess):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "考试已超时，请提交试卷")
    if not db.execute(
        select(ExamQuestion.id).where(
            ExamQuestion.exam_definition_id == sess.exam_definition_id,
            ExamQuestion.question_id == qid,
        )
    ).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "题目不属于当前考试")

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
        raise HTTPException(status.HTTP_409_CONFLICT, "数据版本冲突，请刷新后重试")

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
    from app.models.record import ExamResult

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
        except HTTPException:
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试会话不存在")

    e = db.get(ExamDefinition, sess.exam_definition_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试定义不存在")

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
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "考试已结束")

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
    need_review = False
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
    passed = (score >= e.pass_score) if (not need_review and not overtime) else False

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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试会话不存在")
    result = db.execute(select(ExamResult).where(ExamResult.exam_session_id == session_id)).scalar_one_or_none()
    if not result:
        return {"published": False, "message": "成绩尚未生成"}
    if not result.published:
        return {"published": False, "message": "成绩待复核后公布"}
    return {
        "published": True,
        "score": result.score,
        "total_score": result.total_score,
        "passed": result.passed,
        "correct_count": result.correct_count,
        "total_count": result.total_count,
    }


def session_detail(db: Session, user: User, session_id: int) -> dict:
    """按会话 id 返回进行中考试的完整题目与会话状态（用于断点续答）。"""
    sess = db.get(ExamSession, session_id)
    if not sess or sess.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试会话不存在")
    if sess.status != "in_progress":
        # 已交卷，返回状态供前端跳成绩
        return {
            "session_id": sess.id,
            "version": sess.version,
            "status": sess.status,
            "finished": True,
            "answers": sess.answers,
            "remaining_sec": sess.remaining_sec,
            "duration_min": 0,
            "started_at": sess.started_at,
            "questions": [],
            "exam_name": "",
        }
    e = db.get(ExamDefinition, sess.exam_definition_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试定义不存在")
    return _session_payload(db, sess, e)


# ---------- 模拟考试默认规则 ----------
def save_mock_config(db: Session, config: dict) -> dict:
    # 把规则存到 settings（category=mock）
    # 用单个 setting 存 JSON
    import json

    from app.models.system import Setting

    row = db.execute(select(Setting).where(Setting.setting_key == "mock_config")).scalar_one_or_none()
    val = json.dumps(config, ensure_ascii=False)
    if row is None:
        db.add(Setting(setting_key="mock_config", value=val, category="mock", encrypted=False))
    else:
        row.value = val
    db.commit()
    return {"success": True}


def _restrict_mock_config_to_enabled_banks(db: Session, rules: dict) -> dict:
    """把模拟考试规则中的 bank_ids 收敛到「允许用户练习」的题库范围内。

    模拟考试在产品上是练习性质（docs/requirement.md：练习性，不计正式档案；
    用户端文案「可反复练习」），管理员界面对关闭练习的题库也明确标注为
    「仅考试使用」——因此它必须和练习入口保持同一口径，否则用户仍能通过
    模拟考试练到已关闭练习的题库，开关形同虚设。

    规则：配置了 bank_ids 时取其与开放题库的交集；未配置（空/缺省）时
    展开为全部开放题库，避免"空列表 = 不过滤"导致抽到已关闭的题库。
    """
    from app.services.practice_service import enabled_bank_ids

    enabled = enabled_bank_ids(db)
    configured = rules.get("bank_ids") or []
    rules = dict(rules)
    rules["bank_ids"] = [bid for bid in configured if bid in set(enabled)] if configured else enabled
    return rules


def start_mock_exam(db: Session, user: User) -> dict:
    """模拟考试：按默认规则即时生成并开考。

    复用同一用户已有的 ongoing mock 考试定义，避免每开考一次就新增一行
    exam_definitions + N 行 exam_questions 导致数据无界增长。
    """
    import json

    from app.models.system import Setting

    row = db.execute(select(Setting).where(Setting.setting_key == "mock_config")).scalar_one_or_none()
    config = (
        json.loads(row.value)
        if row and row.value
        else {
            "type_quota": {"单选题": 10, "多选题": 5, "判断题": 5},
            "max_questions": 30,
        }
    )
    config = _restrict_mock_config_to_enabled_banks(db, config)
    settings = get_settings(db, "exam")
    # 复用当前用户进行中的 mock 考试；无则新建。
    # 必须按 created_by 收敛到本人：mock 定义为全局可见，若不过滤，
    # 首个开考用户创建的定义会被之后所有用户复用，导致所有人共用同一套已固化试题。
    existing = (
        db.execute(
            select(ExamDefinition).where(
                ExamDefinition.type == "mock",
                ExamDefinition.status == "ongoing",
                ExamDefinition.created_by == user.id,
            )
        )
        .scalars()
        .first()
    )
    if existing:
        # 既有定义复用了旧的 rules；此处同步为按当前开放题库收敛后的范围，
        # 否则管理员后来关闭某题库，复用中的模拟考试仍会抽到它。
        existing.rules = _restrict_mock_config_to_enabled_banks(db, dict(existing.rules or {}))
        db.commit()
        return start_exam(db, user, existing.id)
    e = ExamDefinition(
        name="模拟考试",
        type="mock",
        rules=config,
        group_ids=None,
        start_at=None,
        end_at=None,
        duration_min=int(settings.get("default_exam_duration_min", "90")),
        pass_score=float(settings.get("default_pass_score", "60")),
        max_attempts=0,
        show_score_immediately=True,
        show_analysis=True,
        need_review=False,
        status="ongoing",
        created_by=user.id,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return start_exam(db, user, e.id)


# ---------- 管理端：试卷模板 ----------
def list_templates(db: Session) -> list[dict]:
    rows = db.execute(select(PaperTemplate).order_by(PaperTemplate.id.desc())).scalars().all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "mode": t.mode,
            "config": t.config,
            "group_ids": t.group_ids,
            "question_count": len(t.question_ids or []),
            "created_at": t.created_at,
        }
        for t in rows
    ]


def preview_paper(db: Session, config: dict, scope: set[int] | None = None) -> dict:
    """预览规则组卷结果（不落库）。

    Args:
        db: 数据库会话。
        config: 组卷规则。
        scope: 调用者数据范围分组 id；None 表示不限制（超级管理员）。
    """
    paper = generate_paper(db, config, scope)
    qids = paper["question_ids"]
    questions = []
    question_map = {q.id: q for q in db.execute(select(Question).where(Question.id.in_(qids))).scalars().all()}
    for qid in qids:
        q = question_map.get(qid)
        if q:
            questions.append(
                {
                    "id": q.id,
                    "type": q.type,
                    "question": q.question[:40],
                    "score": paper["scores"].get(qid, 2),
                    "difficulty": q.difficulty,
                }
            )
    return {
        "count": paper["count"],
        "total_score": paper["total_score"],
        "question_ids": qids,
        "questions": questions,
    }


def create_template(db: Session, payload, user: User, scope: set[int] | None = None) -> dict:
    paper = generate_paper(db, payload.config, scope)
    tpl = PaperTemplate(
        name=payload.name,
        mode=payload.mode,
        config=payload.config,
        group_ids=payload.group_ids,
        question_ids=paper["question_ids"],
        created_by=user.id,
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return {"id": tpl.id, "name": tpl.name, "count": paper["count"]}


def delete_template(db: Session, template_id: int) -> None:
    """删除试卷模板；被正式考试引用时拒绝删除（保持考试可追溯）。"""
    tpl = db.get(PaperTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    in_use = db.execute(
        select(func.count()).select_from(ExamDefinition).where(ExamDefinition.paper_template_id == template_id)
    ).scalar_one()
    if in_use:
        raise HTTPException(status_code=409, detail=f"模板已被 {in_use} 场考试引用，无法删除")
    db.delete(tpl)
    db.commit()


# ---------- 管理端：正式考试 ----------
def list_exams(db: Session, scope: set[int] | None = None, status_: str | None = None, limit: int = 500) -> list[dict]:
    """管理端正式考试列表。scope 非 None（部门管理员）时仅返回指派分组落在
    其子树内、或无指派（全量）的考试由 super_admin 可见——这里按 group_ids 与 scope 取交集过滤，
    无指派的考试仅 super_admin 可见（避免 dept_admin 看到未指派给其部门的考试）。

    status_ 为 None 时返回全部状态（含已归档）；显式传入时按状态筛选。
    limit 给出返回条数上限，避免数据增长后一次性载入整表。
    """
    stmt = select(ExamDefinition).where(ExamDefinition.type == "formal")
    if status_:
        stmt = stmt.where(ExamDefinition.status == status_)
    # 注意：部门管理员的 group_ids 过滤在 Python 侧完成（JSON 列无法可靠下推），
    # 因此这里取 limit 的若干倍作为候选，避免过滤后结果过少。
    fetch = limit if scope is None else limit * 4
    rows = db.execute(stmt.order_by(ExamDefinition.id.desc()).limit(fetch)).scalars().all()
    out = []
    for e in rows:
        if scope is not None:
            e_groups = e.group_ids or []
            if not e_groups or not set(e_groups).issubset(scope):
                # 无指派考试默认全员可见，但部门管理员不应看到此类全局考试
                continue
        out.append(_exam_admin_brief(e))
        if len(out) >= limit:
            break
    return out


def _check_exam_scope(db: Session, e: ExamDefinition, scope: set[int] | None) -> None:
    """部门管理员操作考试前的归属校验：考试指派分组须与 scope 有交集。"""
    if scope is None:
        return
    e_groups = e.group_ids or []
    if not e_groups or not set(e_groups).issubset(scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权操作该考试")


def _validate_exam_group_ids(db: Session, group_ids: list[int] | None, scope: set[int] | None) -> None:
    """创建/更新考试时校验指派分组均在调用者数据范围内（防指派到其他部门）。"""
    if scope is not None and not group_ids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "部门管理员必须指定本部门考试分组")
    if not group_ids:
        return
    existing = {row[0] for row in db.execute(select(Group.id).where(Group.id.in_(group_ids))).all()}
    if existing != set(group_ids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "考试指派分组不存在")
    if scope is None:
        return
    for gid in group_ids:
        if gid not in scope:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权指派该分组")


def _validate_exam_question_scope(
    db: Session,
    manual_questions: list[int] | None,
    rules: dict | None,
    paper_template_id: int | None,
    scope: set[int] | None,
) -> None:
    """防止部门管理员把范围外题目带入其考试。"""
    if scope is None:
        return
    if paper_template_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "部门管理员不能使用全局试卷模板")
    if manual_questions:
        rows = db.execute(
            select(Question.id, Question.group_id, QuestionBank.group_id)
            .join(QuestionBank, QuestionBank.id == Question.bank_id, isouter=True)
            .where(Question.id.in_(manual_questions))
        ).all()
        allowed = {row[0] for row in rows if row[1] in scope or row[2] in scope}
        if allowed != set(manual_questions):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "考试包含范围外题目")
        return
    source_groups = (rules or {}).get("group_ids") or []
    if not source_groups or not set(source_groups).issubset(scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "部门管理员组卷必须限定本部门题目")
    source_banks = (rules or {}).get("bank_ids") or []
    if source_banks:
        bank_groups = {
            row[0] for row in db.execute(select(QuestionBank.group_id).where(QuestionBank.id.in_(source_banks))).all()
        }
        if not bank_groups or not bank_groups.issubset(scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "组卷包含范围外题库")


def list_results(
    db: Session,
    exam_id: int | None = None,
    scope: set[int] | None = None,
    limit: int = 500,
    outcome: str | None = None,
) -> list[dict]:
    """管理端：考试成绩列表（JOIN 一次取齐关联信息，消除 N+1）。

    scope 非 None（部门管理员）时仅返回其数据范围内用户的成绩，杜绝跨部门窥探成绩。
    保留返回 list 的契约：前端按全部成绩做客户端关键字过滤，未引入分页控件，
    故不改变响应结构；性能瓶颈（逐行 db.get）已由 JOIN 消除。

    outcome 为状态筛选（None=全部）：passed/failed 按是否及格，
    pending 为待复核（need_review=True 且未公布），published 为已公布。
    """
    from app.core.deps import users_in_scope
    from app.models.exam import ExamDefinition
    from app.models.record import ExamResult, ExamSession
    from app.models.user import User

    stmt = (
        select(
            ExamResult,
            ExamDefinition.name.label("exam_name"),
            User.email.label("user_email"),
            User.name.label("user_name"),
            ExamSession.submitted_at.label("submitted_at"),
        )
        .join(ExamDefinition, ExamDefinition.id == ExamResult.exam_definition_id, isouter=True)
        .join(User, User.id == ExamResult.user_id, isouter=True)
        .join(ExamSession, ExamSession.id == ExamResult.exam_session_id, isouter=True)
    )
    if exam_id:
        stmt = stmt.where(ExamResult.exam_definition_id == exam_id)
    if outcome == "passed":
        stmt = stmt.where(ExamResult.passed.is_(True))
    elif outcome == "failed":
        stmt = stmt.where(ExamResult.passed.is_(False))
    elif outcome == "pending":
        stmt = stmt.where(ExamResult.published.is_(False), ExamResult.need_review.is_(True))
    elif outcome == "published":
        stmt = stmt.where(ExamResult.published.is_(True))
    if scope is not None:
        if not exam_id:
            allowed_exam_ids = {
                row[0]
                for row in db.execute(select(ExamDefinition.id, ExamDefinition.group_ids)).all()
                if row[1] and set(row[1]).issubset(scope)
            }
            if not allowed_exam_ids:
                return []
            stmt = stmt.where(ExamResult.exam_definition_id.in_(allowed_exam_ids))
        else:
            exam = db.get(ExamDefinition, exam_id)
            if not exam or not exam.group_ids or not set(exam.group_ids).issubset(scope):
                return []
        scoped_user_ids = users_in_scope(db, scope)
        if not scoped_user_ids:
            return []
        stmt = stmt.where(ExamResult.user_id.in_(scoped_user_ids))

    rows = db.execute(stmt.order_by(ExamResult.id.desc()).limit(limit)).all()
    out = []
    for r in rows:
        res = r[0]
        out.append(
            {
                "id": res.id,
                "exam_name": r.exam_name or "",
                "user_email": r.user_email or "",
                "user_name": r.user_name or "",
                "score": res.score,
                "total_score": res.total_score,
                "correct_count": res.correct_count,
                "total_count": res.total_count,
                "passed": res.passed,
                "published": res.published,
                "need_review": res.need_review,
                "submitted_at": r.submitted_at,
            }
        )
    return out


def create_exam(db: Session, payload, user: User, scope: set[int] | None = None) -> dict:
    if scope is not None and payload.type != "formal":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "部门管理员只能创建正式考试")
    _validate_exam_group_ids(db, payload.group_ids, scope)
    _validate_exam_question_scope(db, payload.manual_questions, payload.rules, payload.paper_template_id, scope)
    e = ExamDefinition(
        name=payload.name,
        type=payload.type,
        paper_template_id=payload.paper_template_id,
        manual_questions=payload.manual_questions,
        rules=payload.rules or {},
        group_ids=payload.group_ids,
        start_at=payload.start_at,
        end_at=payload.end_at,
        duration_min=payload.duration_min,
        pass_score=payload.pass_score,
        max_attempts=payload.max_attempts,
        show_score_immediately=payload.show_score_immediately,
        show_analysis=payload.show_analysis,
        need_review=payload.need_review,
        status="draft",
        created_by=user.id,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return {"id": e.id, "name": e.name, "status": e.status}


def update_exam(db: Session, exam_id: int, payload: dict, scope: set[int] | None = None) -> dict:
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试不存在")
    _check_exam_scope(db, e, scope)
    if "group_ids" in payload:
        _validate_exam_group_ids(db, payload.get("group_ids"), scope)
    _validate_exam_question_scope(
        db,
        payload.get("manual_questions", e.manual_questions),
        payload.get("rules", e.rules),
        payload.get("paper_template_id", e.paper_template_id),
        scope,
    )
    for k in (
        "name",
        "rules",
        "group_ids",
        "start_at",
        "end_at",
        "duration_min",
        "pass_score",
        "max_attempts",
        "show_score_immediately",
        "show_analysis",
        "need_review",
        "manual_questions",
        "paper_template_id",
    ):
        if k in payload:
            setattr(e, k, payload[k])
    db.commit()
    db.refresh(e)
    return {"id": e.id, "name": e.name, "status": e.status}


def delete_exam(db: Session, exam_id: int, scope: set[int] | None = None) -> None:
    """删除考试。

    可删除条件：无任何作答记录（ExamSession）。草稿与已发布但无人作答的考试
    （典型如测试考试）都可删除；一旦有作答记录则拒绝，改用「归档」以保留成绩可追溯。
    """
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试不存在")
    _check_exam_scope(db, e, scope)
    sessions = db.execute(
        select(func.count()).select_from(ExamSession).where(ExamSession.exam_definition_id == exam_id)
    ).scalar_one()
    if sessions:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"该考试已有 {sessions} 条作答记录，无法删除；请改用「归档」（归档后对用户隐藏，成绩仍可追溯）",
        )
    # 一并清理成绩/复核等无会话关联的残留（防御历史脏数据导致外键失败）
    result_ids = select(ExamResult.id).where(ExamResult.exam_definition_id == exam_id).scalar_subquery()
    db.execute(delete(ShortAnswerReview).where(ShortAnswerReview.exam_result_id.in_(result_ids)))
    db.execute(delete(ExamResult).where(ExamResult.exam_definition_id == exam_id))
    db.execute(delete(ExamQuestion).where(ExamQuestion.exam_definition_id == exam_id))
    db.delete(e)
    db.commit()


def archive_exam(db: Session, exam_id: int, scope: set[int] | None = None) -> dict:
    """归档考试：对用户隐藏（不再出现在可用列表），但保留定义与成绩记录。

    用于已发布且已有作答记录、不能删除的考试（如测试考试）。
    """
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试不存在")
    _check_exam_scope(db, e, scope)
    e.status = "archived"
    db.commit()
    db.refresh(e)
    return {"id": e.id, "name": e.name, "status": e.status}


def unarchive_exam(db: Session, exam_id: int, scope: set[int] | None = None) -> dict:
    """取消归档：恢复为已发布（重新对用户可见）。"""
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试不存在")
    _check_exam_scope(db, e, scope)
    if e.status != "archived":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该考试未处于归档状态")
    e.status = "published"
    db.commit()
    db.refresh(e)
    return {"id": e.id, "name": e.name, "status": e.status}


def publish_exam(db: Session, exam_id: int, scope: set[int] | None = None, bg=None) -> dict:
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试不存在")
    _check_exam_scope(db, e, scope)
    e.status = "published"
    db.commit()
    db.refresh(e)
    if bg is not None:
        stmt = select(User).where(User.status == "active")
        if e.group_ids:
            stmt = (
                stmt.outerjoin(UserGroup, UserGroup.user_id == User.id)
                .where(or_(UserGroup.group_id.in_(e.group_ids), User.dept_group_id.in_(e.group_ids)))
                .distinct()
            )
        recipients = db.execute(stmt).scalars().all()
        settings = get_settings(db)
        for recipient in recipients:
            bg.add_task(mail_service.send_exam_publish, settings, recipient.email, e.name, e.end_at or "考试结束前")
    return {"id": e.id, "status": e.status}


# ---------- 管理端：模拟考试设置 ----------
def get_mock_config_full(db: Session) -> dict:
    import json

    from app.models.system import Setting

    row = db.execute(select(Setting).where(Setting.setting_key == "mock_config")).scalar_one_or_none()
    if row and row.value:
        try:
            return json.loads(row.value)
        except (ValueError, TypeError):
            pass
    return {
        "type_quota": {"单选题": 10, "多选题": 5, "判断题": 5, "填空题": 3, "简答题": 2},
        "difficulty_dist": {},
        "group_ids": [],
        "bank_ids": [],
        "tags": [],
        "allow_duplicate": False,
        "max_questions": 30,
    }
