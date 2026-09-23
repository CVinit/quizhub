"""考试会话：可用考试列表、开考与卷面固化、断点续答。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import subtree_map
from app.core.errors import DomainError
from app.core.status import BAD_REQUEST, CONFLICT, FORBIDDEN, NOT_FOUND
from app.models.exam import ExamDefinition, ExamQuestion, PaperTemplate
from app.models.group import UserGroup
from app.models.question import DEFAULT_QUESTION_SCORE, Question
from app.models.record import ExamResult, ExamSession
from app.models.user import User
from app.services.exam.common import (
    _attempt_counts,
    _count_attempts,
    _exam_brief,
    _exam_question_counts,
    _now,
    _parse_time,
    _user_can_access_exam,
    _within_time_window,
)
from app.services.exam.scoring import _recover_stuck_scoring
from app.services.paper_service import generate_paper

# ---------- 用户端：可用考试 ----------
# 可用考试列表上限。可见性判定依赖 JSON 列（指派分组），无法可靠下推 SQL，
# 因此与管理端 list_exams 同口径：按 limit 的若干倍取候选，再在 Python 侧过滤。
_AVAILABLE_MAX = 200
_AVAILABLE_FETCH_FACTOR = 4


def list_available(db: Session, user: User) -> list[dict]:
    """返回当前用户可参加的考试。

    仅返回正式考试（type=formal）；模拟考试（type=mock）为用户自助发起，不经此列表，
    避免用户 A 的模拟考出现在用户 B 的可用列表中污染数据。
    """

    user_group_ids = {r[0] for r in db.execute(select(UserGroup.group_id).where(UserGroup.user_id == user.id)).all()}
    if user.dept_group_id:
        user_group_ids.add(user.dept_group_id)

    now = datetime.now(timezone.utc)
    # 分组树只取一次：指派到父分组时要展开子树后才能判定成员可见性
    subtree = subtree_map(db)
    rows = (
        db.execute(
            select(ExamDefinition)
            .where(
                ExamDefinition.status.in_(["published", "ongoing"]),
                ExamDefinition.type == "formal",  # 仅正式考试进入可用列表
            )
            .order_by(ExamDefinition.id.desc())
            .limit(_AVAILABLE_MAX * _AVAILABLE_FETCH_FACTOR)
        )
        .scalars()
        .all()
    )

    # 一次性取回本人对所有候选考试的已确认尝试次数，避免逐场查询（N+1）
    attempt_map = _attempt_counts(db, [e.id for e in rows], user.id)
    # 先筛出可见考试，再批量算题数：`_exam_brief(...)` 逐场 COUNT 会退化成
    # 「考试数 × 1 次查询」，此处改为一次 GROUP BY + 一次模板 IN 查询。
    visible: list[tuple[ExamDefinition, str, int]] = []
    for e in rows:
        if not _user_can_access_exam(e, user_group_ids, subtree):
            continue
        ok, state = _within_time_window(e, now)
        if not ok:
            visible.append((e, state, 0))
            continue
        attempts = attempt_map.get(e.id, 0)
        if e.max_attempts and attempts >= e.max_attempts:
            visible.append((e, "max_reached", attempts))
            continue
        visible.append((e, "available", attempts))
    visible = visible[:_AVAILABLE_MAX]
    counts = _exam_question_counts(db, [e for e, _state, _attempts in visible])
    return [_exam_brief(e, state, attempts, total_questions=counts.get(e.id, 0)) for e, state, attempts in visible]


# ---------- 开考 ----------
def start_exam(db: Session, user: User, exam_id: int) -> dict:
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise DomainError(NOT_FOUND, "考试不存在")
    if e.status not in ("published", "ongoing"):
        raise DomainError(BAD_REQUEST, "考试未开放")
    # 权限：正式考试须在指派分组内且在时段窗内（start_exam 与 list_available 复用同一校验，
    # 防止用户猜枚举 exam_id 开考未指派/未到时段的考试）
    if e.type == "formal":
        user_group_ids = {
            r[0] for r in db.execute(select(UserGroup.group_id).where(UserGroup.user_id == user.id)).all()
        }
        if user.dept_group_id:
            user_group_ids.add(user.dept_group_id)
        if not _user_can_access_exam(e, user_group_ids, subtree_map(db)):
            raise DomainError(FORBIDDEN, "您不在该考试指派范围内")
        ok, _state = _within_time_window(e, datetime.now(timezone.utc))
        if not ok:
            raise DomainError(BAD_REQUEST, "考试不在开放时段")
    elif e.created_by != user.id:
        # 模拟考试定义是「开考用户私有」的：mock 定义全局可见（list_available 已按 type 过滤），
        # 若不校验 created_by，任何登录用户枚举 exam_id 就能启动他人的模拟考并固化其试题。
        raise DomainError(NOT_FOUND, "考试不存在")
    # 是否有进行中的会话
    ongoing = db.execute(
        select(ExamSession).where(
            ExamSession.exam_definition_id == exam_id,
            ExamSession.user_id == user.id,
            ExamSession.status.in_(["in_progress", "scoring"]),
        )
    ).scalar_one_or_none()
    if ongoing:
        # scoring 但无对应成绩记录 → 可能是进程崩溃残留，需回收为可续答。
        # 但必须由 `_recover_stuck_scoring` 的超时守卫决定：原实现无条件把 scoring 改回
        # in_progress，等于绕过了 30 分钟保护，考生可在结算中途重新作答/改答案。
        if ongoing.status == "scoring":
            has_result = db.execute(select(ExamResult.id).where(ExamResult.exam_session_id == ongoing.id)).first()
            if not has_result:
                _recover_stuck_scoring(db, user_id=user.id)
                db.refresh(ongoing)
                if ongoing.status != "in_progress":
                    raise DomainError(CONFLICT, "试卷正在结算中，请稍后重试")
        return _session_payload(db, ongoing, e)

    # 未结束的会话也算占用次数
    attempts = _count_attempts(db, exam_id, user.id)
    if e.max_attempts and attempts >= e.max_attempts:
        raise DomainError(BAD_REQUEST, "已达最大尝试次数")

    # 固化题目（若尚未固化）；空卷直接拒绝，避免考生交白卷后按 total_score=0 判分
    qids = _ensure_exam_questions(db, e)
    if not qids:
        raise DomainError(BAD_REQUEST, "该考试没有可用题目，请联系管理员")

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
            raise DomainError(BAD_REQUEST, "同一考试不能重复添加题目")
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

    分值来源优先级：调用方传入的 scores（规则组卷按当次抽题结果算好的映射）> 题目行自身的
    Question.score > DEFAULT_QUESTION_SCORE。**必须**回落到题目行的分值：手工选题与
    「模板已含 question_ids」两条路径都传 scores=None，若直接用常量兜底，题目配置的分值
    会被整体改写成 2.0，使 total_score 与及格判定双错。
    """
    if len(qids) != len(set(qids)):
        raise DomainError(BAD_REQUEST, "同一考试不能重复添加题目")
    question_scores = {
        row[0]: (float(row[1]) if row[1] is not None else DEFAULT_QUESTION_SCORE)
        for row in db.execute(select(Question.id, Question.score).where(Question.id.in_(qids))).all()
    }
    missing = set(qids) - set(question_scores)
    if missing:
        raise DomainError(BAD_REQUEST, "考试包含不存在的题目")
    # 并发兜底：唯一约束 + 插入前复核，避免双请求都读到 existing=[] 后重复插入
    already = db.execute(select(ExamQuestion.id).where(ExamQuestion.exam_definition_id == exam_id).limit(1)).first()
    if already:
        return
    pending = []
    for seq, qid in enumerate(qids):
        score = (scores or {}).get(qid, question_scores.get(qid, DEFAULT_QUESTION_SCORE))
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


def _remaining_seconds(sess: ExamSession, e: ExamDefinition) -> int:
    """按服务端真实起点实时计算剩余秒数。

    `ExamSession.remaining_sec` 只在开考时写入一次、从不递减，直接回传会让「断点续考」
    重新开始整场倒计时（前端据此初始化计时器），而服务端 `_is_overtime` 仍按
    `started_at + duration_min` 判定，超时交卷会被静默置 0 分。因此这里以截止时间为准
    实时换算；该列降级为历史数据兼容用的兜底值。

    Args:
        sess: 考试会话。
        e: 考试定义（提供时长与截止时间）。

    Returns:
        剩余秒数，最小为 0。
    """
    try:
        deadline = _parse_time(sess.started_at) + timedelta(minutes=e.duration_min)
        if e.end_at:
            deadline = min(deadline, _parse_time(e.end_at))
    except DomainError:
        # 历史脏数据（started_at 无法解析）：返回存量值，避免整个续答接口 400
        return int(sess.remaining_sec or 0)
    return max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))


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
        "remaining_sec": _remaining_seconds(sess, e),
        "duration_min": e.duration_min,
        "started_at": sess.started_at,
        "questions": questions,
        "exam_name": e.name,
    }


def session_detail(db: Session, user: User, session_id: int) -> dict:
    """按会话 id 返回进行中考试的完整题目与会话状态（用于断点续答）。"""
    sess = db.get(ExamSession, session_id)
    if not sess or sess.user_id != user.id:
        raise DomainError(NOT_FOUND, "考试会话不存在")
    if sess.status != "in_progress":
        # 已交卷，返回状态供前端跳成绩；剩余时间对已结束会话无意义，固定为 0
        return {
            "session_id": sess.id,
            "version": sess.version,
            "status": sess.status,
            "finished": True,
            "answers": sess.answers,
            "remaining_sec": 0,
            "duration_min": 0,
            "started_at": sess.started_at,
            "questions": [],
            "exam_name": "",
        }
    e = db.get(ExamDefinition, sess.exam_definition_id)
    if not e:
        raise DomainError(NOT_FOUND, "考试定义不存在")
    return _session_payload(db, sess, e)
