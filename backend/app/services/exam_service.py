"""考试业务：会话、逐题落库（乐观锁）、结算、模拟/正式考试。

- 模拟考试：套用默认规则（mock-config），用户自助开考。
- 正式考试：管理员发布指派分组，单场规则。
- 简答需复核时 published=false，复核完成后公布。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.exam import ExamDefinition, ExamQuestion, PaperTemplate
from app.models.question import Question
from app.models.record import ExamResult, ExamSession, ShortAnswerReview
from app.models.user import User
from app.services.grading import grade
from app.services.paper_service import generate_paper
from app.services.system_service import get_settings

SESSION_STATUS = ("in_progress", "submitted", "scoring", "scored", "reviewed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_can_access_exam(e: ExamDefinition, user_group_ids: list[int]) -> bool:
    """考试指派分组与用户分组有交集（空指派表示不限）。"""
    e_groups = e.group_ids or []
    return not e_groups or bool(set(e_groups).intersection(user_group_ids))


def _within_time_window(e: ExamDefinition, now: str) -> tuple[bool, str]:
    """正式考试时段校验。返回 (是否在窗口内, 状态)。"""
    if e.type != "formal":
        return True, ""
    if e.start_at and now < e.start_at:
        return False, "not_started"
    if e.end_at and now > e.end_at:
        return False, "ended"
    return True, ""


# ---------- 用户端：可用考试 ----------
def list_available(db: Session, user: User) -> list[dict]:
    """返回当前用户可参加的考试。

    仅返回正式考试（type=formal）；模拟考试（type=mock）为用户自助发起，不经此列表，
    避免用户 A 的模拟考出现在用户 B 的可用列表中污染数据。
    """
    from app.models.group import UserGroup

    user_group_ids = [r[0] for r in db.execute(select(UserGroup.group_id).where(UserGroup.user_id == user.id)).all()]

    now = _now()
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

        user_group_ids = [
            r[0] for r in db.execute(select(UserGroup.group_id).where(UserGroup.user_id == user.id)).all()
        ]
        if not _user_can_access_exam(e, user_group_ids):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "您不在该考试指派范围内")
        ok, _state = _within_time_window(e, _now())
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
                _recover_stuck_scoring(db)
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
    db.commit()
    db.refresh(sess)
    return _session_payload(db, sess, e)


def _ensure_exam_questions(db: Session, e: ExamDefinition) -> list[int]:
    """确保 exam_questions 已固化，返回题目 id 列表。"""
    existing = db.execute(select(ExamQuestion).where(ExamQuestion.exam_definition_id == e.id)).scalars().all()
    if existing:
        return sorted({eq.question_id for eq in existing})

    # 确定题目来源
    if e.manual_questions:
        qids = list(e.manual_questions)
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
    for seq, qid in enumerate(qids):
        score = (scores or {}).get(qid, 2)
        db.add(ExamQuestion(exam_definition_id=exam_id, question_id=qid, seq=seq, score=score, shuffle_map=None))
    db.commit()


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
        for q in db.execute(select(Question).where(Question.id.in_(qids))).scalars().all():
            q_map[q.id] = q
    questions = []
    for eq in eqs:
        q = q_map.get(eq.question_id)
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

    原子 UPDATE ... WHERE id=? AND version=? 形如架构决策：affected=0 → 409。
    """
    sess = db.get(ExamSession, session_id)
    if not sess or sess.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试会话不存在")
    if sess.status != "in_progress":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "考试已结束，无法作答")

    # 合并答案（基于本次读取的 answers 快照）
    answers = dict(sess.answers or {})
    answers[str(qid)] = {"answer": answer, "answered_at": _now()}
    new_version = version + 1

    # 原子条件更新：仅当数据库当前 version == 期望 version 时才写入
    result = db.execute(
        ExamSession.__table__.update()
        .where(ExamSession.id == session_id, ExamSession.version == version)
        .values(answers=answers, version=new_version)
    )
    if result.rowcount == 0:
        # version 已被他人改动 → 冲突
        raise HTTPException(status.HTTP_409_CONFLICT, "数据版本冲突，请刷新后重试")

    db.commit()
    return {"version": new_version}


# ---------- 交卷结算 ----------
# scoring 状态超过该秒数视为崩溃残留，可被回收（启动刷新时或下次开考时）
_SCORING_TIMEOUT_SEC = 30 * 60


def _recover_stuck_scoring(db: Session) -> int:
    """回收超时的 scoring 会话：将其重置回 in_progress，释放被卡死的尝试次数。

    返回回收的会话数。仅在单进程串行写的前提下安全（SQLite WAL）。
    """
    from datetime import datetime, timezone

    cutoff_iso = datetime.now(timezone.utc).timestamp() - _SCORING_TIMEOUT_SEC
    rows = db.execute(select(ExamSession).where(ExamSession.status == "scoring")).scalars().all()
    recovered = 0
    for s in rows:
        try:
            ts = datetime.fromisoformat(s.submitted_at or s.started_at).timestamp()
        except (ValueError, TypeError):
            continue
        if ts < cutoff_iso:
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
    overtime = False
    if e.type == "formal" and e.end_at and _now() > e.end_at:
        overtime = True

    # 幂等保护：原子把状态置为 scoring，若已非 in_progress 则 rowcount=0，避免重复提交
    locked = db.execute(
        ExamSession.__table__.update()
        .where(ExamSession.id == session_id, ExamSession.status == "in_progress")
        .values(status="scoring", submitted_at=_now())
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
                "overtime": False,
            }
        if existing and not existing.published:
            return {"need_review": True, "message": "含简答题，待管理员复核后公布成绩", "overtime": False}
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "考试已结束")

    eqs = db.execute(select(ExamQuestion).where(ExamQuestion.exam_definition_id == e.id)).scalars().all()

    # 批量取题目，消除 N+1
    qids = [eq.question_id for eq in eqs]
    q_map: dict[int, Question] = {}
    if qids:
        for q in db.execute(select(Question).where(Question.id.in_(qids))).scalars().all():
            q_map[q.id] = q

    correct_count = 0
    total_count = len(eqs)
    objective_score = 0.0
    total_score = sum(eq.score for eq in eqs) or 100
    need_review = False
    short_answers: list[tuple[int, str, str]] = []

    answers = sess.answers or {}
    # 超时则客观题不计分，但仍记录作答并走复核流程（若有简答）
    for eq in eqs:
        q = q_map.get(eq.question_id)
        if not q:
            continue
        user_ans = answers.get(str(q.id), {}).get("answer")
        if q.type == "简答题":
            need_review = True
            short_answers.append((q.id, user_ans or "", q.answer or ""))
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
    settings = get_settings(db, "exam")
    # 复用当前用户进行中的 mock 考试；无则新建
    existing = (
        db.execute(
            select(ExamDefinition).where(
                ExamDefinition.type == "mock",
                ExamDefinition.status == "ongoing",
            )
        )
        .scalars()
        .first()
    )
    if existing:
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


def preview_paper(db: Session, config: dict) -> dict:
    """预览规则组卷结果（不落库）。"""
    paper = generate_paper(db, config)
    qids = paper["question_ids"]
    questions = []
    for qid in qids:
        q = db.get(Question, qid)
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


def create_template(db: Session, payload, user: User) -> dict:
    paper = generate_paper(db, payload.config)
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


# ---------- 管理端：正式考试 ----------
def list_exams(db: Session) -> list[dict]:
    # 仅返回正式考试，模拟考为自助发起、不进管理列表，避免 mock 污染
    rows = (
        db.execute(select(ExamDefinition).where(ExamDefinition.type == "formal").order_by(ExamDefinition.id.desc()))
        .scalars()
        .all()
    )
    return [_exam_brief(e, e.status) for e in rows]


def list_results(db: Session, exam_id: int | None = None) -> list[dict]:
    """管理端：考试成绩列表（JOIN 一次取齐关联信息，消除 N+1）。

    保留返回 list 的契约：前端按全部成绩做客户端关键字过滤，未引入分页控件，
    故不改变响应结构；性能瓶颈（逐行 db.get）已由 JOIN 消除。
    """
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

    rows = db.execute(stmt.order_by(ExamResult.id.desc())).all()
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


def create_exam(db: Session, payload, user: User) -> dict:
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


def update_exam(db: Session, exam_id: int, payload: dict) -> dict:
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试不存在")
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


def publish_exam(db: Session, exam_id: int) -> dict:
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "考试不存在")
    e.status = "published"
    db.commit()
    db.refresh(e)
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
