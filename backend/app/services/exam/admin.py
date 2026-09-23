"""管理端正式考试：列表/CRUD、数据范围校验、归档发布、作废重固化。"""

from __future__ import annotations

import logging
from typing import cast

from fastapi import status
from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.group import Group, UserGroup
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult, ExamSession, ShortAnswerReview
from app.models.user import User
from app.services import mail_service
from app.services.exam.common import _exam_brief, _exam_question_counts, _expand_groups, _parse_time, exam_in_scope
from app.services.grading import is_passed
from app.services.system_service import get_settings

logger = logging.getLogger("quizhub")


def _exam_admin_brief(e: ExamDefinition, db: Session | None = None, total_questions: int | None = None) -> dict:
    """管理端考试列表项：在用户端摘要基础上补齐可编辑配置。

    编辑弹窗需要回填组卷方式、题型配比、来源题库与指派分组；缺失这些字段会导致
    打开编辑时配比/题库显示为空，保存后原配置被清空。
    """
    brief = _exam_brief(e, e.status, db=db, total_questions=total_questions)
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


# ---------- 管理端：正式考试 ----------
def list_exams(db: Session, scope: set[int] | None = None, status_: str | None = None, limit: int = 500) -> list[dict]:
    """管理端正式考试列表。scope 非 None（部门管理员）时仅返回**全部**指派分组都落在
    其子树内的考试；无指派的考试仅 super_admin 可见——避免 dept_admin 看到未指派给其部门的考试。
    判定口径是子集（`set(group_ids).issubset(scope)`），与 `_check_exam_scope` 一致。

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
    visible: list[ExamDefinition] = []
    for e in rows:
        # 口径统一由 exam_in_scope 表达（子集）：与概览计数、_check_exam_scope 共用，
        # 否则会出现「概览算得到、列表看不到、编辑 403」的自相矛盾。
        if not exam_in_scope(e.group_ids, scope):
            # 无指派考试默认全员可见，但部门管理员不应看到此类全局考试
            continue
        visible.append(e)
        if len(visible) >= limit:
            break
    # 批量取题数：原实现逐场 `_exam_question_count` → 最多 500/2000 次 COUNT 查询
    counts = _exam_question_counts(db, visible)
    return [_exam_admin_brief(e, total_questions=counts.get(e.id, 0)) for e in visible]


def _check_exam_scope(db: Session, e: ExamDefinition, scope: set[int] | None) -> None:
    """部门管理员操作考试前的归属校验：考试的全部分组都须落在 scope 内。

    口径是**子集**而非"有交集"：只要有一个指派分组在调用者范围之外，就说明该考试
    跨出了其管辖范围（例如共同指派给两个部门的考试，两个部门管理员都无权改）。
    判定统一由 `exam_in_scope` 表达，与概览计数、考试列表共用同一实现。
    """
    if not exam_in_scope(e.group_ids, scope):
        raise DomainError(status.HTTP_403_FORBIDDEN, "无权操作该考试")


def _validate_exam_group_ids(db: Session, group_ids: list[int] | None, scope: set[int] | None) -> None:
    """创建/更新考试时校验指派分组均在调用者数据范围内（防指派到其他部门）。"""
    if scope is not None and not group_ids:
        raise DomainError(status.HTTP_403_FORBIDDEN, "部门管理员必须指定本部门考试分组")
    if not group_ids:
        return
    existing = {row[0] for row in db.execute(select(Group.id).where(Group.id.in_(group_ids))).all()}
    if existing != set(group_ids):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "考试指派分组不存在")
    if scope is None:
        return
    for gid in group_ids:
        if gid not in scope:
            raise DomainError(status.HTTP_403_FORBIDDEN, "无权指派该分组")


def _validate_exam_question_scope(
    db: Session,
    manual_questions: list[int] | None,
    rules: dict | None,
    paper_template_id: int | None,
    scope: set[int] | None,
) -> None:
    """防止部门管理员把范围外题目带入其考试。

    范围证据只要求**至少一项**落在 scope 内即可（题库集合或来源分组集合）：
    `rules` 的题型配比在管理端是普通组卷界面，客户端只会提交 bank_ids，
    不会提交 rules["group_ids"]（考试级 group_ids 是"指派范围"，与题目来源无关）。
    若强制要求 rules["group_ids"]，部门管理员将完全无法创建/编辑规则组卷考试。
    两者都缺失时无法证明范围，按 fail-closed 拒绝。
    """
    if scope is None:
        return
    if paper_template_id is not None:
        raise DomainError(status.HTTP_403_FORBIDDEN, "部门管理员不能使用全局试卷模板")
    if manual_questions:
        rows = db.execute(
            select(Question.id, Question.group_id, QuestionBank.group_id)
            .join(QuestionBank, QuestionBank.id == Question.bank_id, isouter=True)
            .where(Question.id.in_(manual_questions))
        ).all()
        allowed = {row[0] for row in rows if row[1] in scope or row[2] in scope}
        if allowed != set(manual_questions):
            raise DomainError(status.HTTP_403_FORBIDDEN, "考试包含范围外题目")
        return
    source_groups = (rules or {}).get("group_ids") or []
    source_banks = (rules or {}).get("bank_ids") or []
    if source_banks:
        bank_groups = {
            row[0] for row in db.execute(select(QuestionBank.group_id).where(QuestionBank.id.in_(source_banks))).all()
        }
        # group_id 为 NULL 的全局题库不在任何部门范围内，同样判 403（fail-closed）
        if not bank_groups or not bank_groups.issubset(scope):
            raise DomainError(status.HTTP_403_FORBIDDEN, "组卷包含范围外题库")
    if source_groups and not set(source_groups).issubset(scope):
        raise DomainError(status.HTTP_403_FORBIDDEN, "组卷包含范围外题目")
    if not source_banks and not source_groups:
        raise DomainError(status.HTTP_403_FORBIDDEN, "部门管理员组卷必须限定本部门题库或题目分组")


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
    from app.core.deps import user_ids_subquery

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
            # 部门管理员：只统计「有指派且全部分组都在自身子树内」的考试。
            # 用 JSON1 在 SQL 内判定，避免把整张 exam_definitions 载入 Python 再过滤
            # （原实现还会把结果拼成无界 IN(...)，大数据量下触及 SQLite 绑定参数上限）。
            json_values = func.json_each(ExamDefinition.group_ids).table_valued("value")
            scoped_exams = select(ExamDefinition.id).where(
                ExamDefinition.group_ids.is_not(None),
                func.json_array_length(ExamDefinition.group_ids) > 0,
                ~exists(select(1).select_from(json_values).where(json_values.c.value.notin_(scope))),
            )
            stmt = stmt.where(ExamResult.exam_definition_id.in_(scoped_exams))
        else:
            exam = db.get(ExamDefinition, exam_id)
            if not exam or not exam.group_ids or not set(exam.group_ids).issubset(scope):
                return []
        stmt = stmt.where(ExamResult.user_id.in_(user_ids_subquery(scope)))

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


def _validate_exam_window(start_at: str | None, end_at: str | None) -> None:
    """校验考试时段：字符串必须可解析，且结束时间晚于开始时间。

    非法值若落库，会一直潜伏到考生取列表/开考时才由 `_parse_time` 抛 400，
    使整场考试对所有学生不可用；因此在创建/更新入口就拦下。
    """
    if not start_at or not end_at:
        return
    if _parse_time(end_at) <= _parse_time(start_at):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "考试结束时间必须晚于开始时间")


def create_exam(db: Session, payload, user: User, scope: set[int] | None = None) -> dict:
    if scope is not None and payload.type != "formal":
        raise DomainError(status.HTTP_403_FORBIDDEN, "部门管理员只能创建正式考试")
    _validate_exam_group_ids(db, payload.group_ids, scope)
    _validate_exam_question_scope(db, payload.manual_questions, payload.rules, payload.paper_template_id, scope)
    _validate_exam_window(payload.start_at, payload.end_at)
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


# 组卷来源字段：任一变化都必须让「已固化的卷面」失效，否则管理员改了配置、
# 考生拿到的仍是旧卷（静默失效，且成绩无法追溯）。


_EXAM_SOURCE_FIELDS = ("rules", "manual_questions", "paper_template_id")


def _source_fields_changed(e: ExamDefinition, payload: dict) -> bool:
    """payload 是否真的改动了组卷来源（按值比较，忽略"字段被原样回传"）。"""
    return any(field in payload and payload[field] != getattr(e, field) for field in _EXAM_SOURCE_FIELDS)


def _deleted_rows(result: object) -> int:
    """DELETE 语句的受影响行数（SQLAlchemy 类型上需 CursorResult 才暴露 rowcount）。"""
    return cast(CursorResult, result).rowcount or 0


def _reset_exam_attempts(db: Session, exam_id: int) -> tuple[dict, list[str]]:
    """作废某考试的全部作答、成绩与已固化卷面，使下次开考按新配置重新固化。

    按外键依赖顺序清理：简答复核 → 成绩 → 会话 → 固化题目。
    不提交事务，由调用方统一收尾。

    Returns:
        (被清理行数, 被清理成绩的 created_at 列表)。时间戳用于重算受影响的每日统计，
        否则排行榜/概览会在下次刷新前继续展示已被作废的成绩。
    """
    timestamps = [
        r[0] for r in db.execute(select(ExamResult.created_at).where(ExamResult.exam_definition_id == exam_id)).all()
    ]
    session_ids = [
        r[0] for r in db.execute(select(ExamSession.id).where(ExamSession.exam_definition_id == exam_id)).all()
    ]

    reviews = 0
    if session_ids:
        reviews += _deleted_rows(
            db.execute(delete(ShortAnswerReview).where(ShortAnswerReview.exam_session_id.in_(session_ids)))
        )
    result_ids = select(ExamResult.id).where(ExamResult.exam_definition_id == exam_id).scalar_subquery()
    reviews += _deleted_rows(
        db.execute(delete(ShortAnswerReview).where(ShortAnswerReview.exam_result_id.in_(result_ids)))
    )
    results = _deleted_rows(db.execute(delete(ExamResult).where(ExamResult.exam_definition_id == exam_id)))
    sessions = _deleted_rows(db.execute(delete(ExamSession).where(ExamSession.exam_definition_id == exam_id)))
    questions = _deleted_rows(db.execute(delete(ExamQuestion).where(ExamQuestion.exam_definition_id == exam_id)))

    counts = {"sessions": sessions, "results": results, "reviews": reviews, "questions": questions}
    return counts, timestamps


def _recompute_published_pass(db: Session, exam_id: int, pass_score: float) -> int:
    """及格线变更后重算已公布成绩的 passed，避免与当前及格线不一致。

    仅动已公布（published）成绩：未公布的成绩在 publish_results 时会用新及格线重算。
    超时考试保持「超时即不及格」语义。
    """
    rows = (
        db.execute(
            select(ExamResult).where(
                ExamResult.exam_definition_id == exam_id,
                ExamResult.published.is_(True),
            )
        )
        .scalars()
        .all()
    )
    changed = 0
    for res in rows:
        # 与 grading.is_passed 同口径：pass_score 为百分制，score/total_score 为原始分
        new_passed = is_passed(res.score, res.total_score, pass_score, overtime=res.overtime)
        if res.passed != new_passed:
            res.passed = new_passed
            changed += 1
    return changed


def update_exam(db: Session, exam_id: int, payload: dict, scope: set[int] | None = None) -> dict:
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise DomainError(status.HTTP_404_NOT_FOUND, "考试不存在")
    _check_exam_scope(db, e, scope)
    if "group_ids" in payload:
        _validate_exam_group_ids(db, payload.get("group_ids"), scope)
    # 只在**组卷来源真的发生变化**时校验题目范围：否则 `payload.get(f, e.f)` 会把
    # 超管配置的存量值（如全局模板）当成本次提交一并复检，使部门管理员连改个考试名
    # 都被 403 永久锁死（存量配置在它被创建时已校验过，无需重复校验）。
    source_changed = _source_fields_changed(e, payload)
    if source_changed:
        _validate_exam_question_scope(
            db,
            payload.get("manual_questions", e.manual_questions),
            payload.get("rules", e.rules),
            payload.get("paper_template_id", e.paper_template_id),
            scope,
        )

    # 改组卷来源且已有固化卷面/作答 → 必须显式确认后作废并重新固化。
    # 这是破坏性操作，因此不做隐式处理：未带 confirm_reset 时返回 409 并给出影响面，
    # 由前端二次确认后重试，避免误改一个下拉框就清空全部成绩。
    reset_counts: dict | None = None
    reset_timestamps: list[str] = []
    if source_changed:
        frozen = db.execute(
            select(func.count()).select_from(ExamQuestion).where(ExamQuestion.exam_definition_id == exam_id)
        ).scalar_one()
        attempts = db.execute(
            select(func.count()).select_from(ExamSession).where(ExamSession.exam_definition_id == exam_id)
        ).scalar_one()
        if frozen or attempts:
            if not payload.get("confirm_reset"):
                raise DomainError(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "code": "exam_reset_required",
                        "msg": (
                            f"该考试已有 {attempts} 条作答记录、{frozen} 道已固化题目。"
                            "修改组卷来源会作废这些作答与成绩，并按新配置重新固化卷面。"
                        ),
                        "counts": {"attempts": attempts, "frozen_questions": frozen},
                    },
                )
            reset_counts, reset_timestamps = _reset_exam_attempts(db, exam_id)

    pass_score_changed = "pass_score" in payload and payload["pass_score"] != e.pass_score

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

    # 时段校验放在 setattr 之后：更新可能只传 start_at 或 end_at，
    # 必须与库中另一侧组合后再判断先后（schema 只能校验成对出现的值）。
    _validate_exam_window(e.start_at, e.end_at)

    if pass_score_changed:
        _recompute_published_pass(db, exam_id, float(e.pass_score))

    db.commit()

    # 作废成绩后重算受影响的每日聚合，避免排行/概览残留已作废的分数。
    # 破坏性清理已在上方 commit 落库：派生统计失败不应把它变成 500（否则管理员会
    # 误以为操作失败而重试），与 question_service._refresh_stats_after_delete 同口径降级为日志。
    if reset_timestamps:
        from app.services import stats_service

        try:
            stats_service.refresh_for_timestamps(db, reset_timestamps)
        except Exception:  # noqa: BLE001  派生统计失败不阻断已提交的作废结果
            db.rollback()
            logger.warning("[exam] 作废作答后统计重算失败，已忽略；下次刷新会兜底重算")

    db.refresh(e)
    out = {"id": e.id, "name": e.name, "status": e.status}
    if reset_counts:
        out["reset"] = reset_counts
    return out


def delete_exam(db: Session, exam_id: int, scope: set[int] | None = None) -> None:
    """删除考试。

    可删除条件：无任何作答记录（ExamSession）。草稿与已发布但无人作答的考试
    （典型如测试考试）都可删除；一旦有作答记录则拒绝，改用「归档」以保留成绩可追溯。
    """
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise DomainError(status.HTTP_404_NOT_FOUND, "考试不存在")
    _check_exam_scope(db, e, scope)
    sessions = db.execute(
        select(func.count()).select_from(ExamSession).where(ExamSession.exam_definition_id == exam_id)
    ).scalar_one()
    if sessions:
        raise DomainError(
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
        raise DomainError(status.HTTP_404_NOT_FOUND, "考试不存在")
    _check_exam_scope(db, e, scope)
    e.status = "archived"
    db.commit()
    db.refresh(e)
    return {"id": e.id, "name": e.name, "status": e.status}


def unarchive_exam(db: Session, exam_id: int, scope: set[int] | None = None) -> dict:
    """取消归档：恢复为已发布（重新对用户可见）。"""
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise DomainError(status.HTTP_404_NOT_FOUND, "考试不存在")
    _check_exam_scope(db, e, scope)
    if e.status != "archived":
        raise DomainError(status.HTTP_400_BAD_REQUEST, "该考试未处于归档状态")
    e.status = "published"
    db.commit()
    db.refresh(e)
    return {"id": e.id, "name": e.name, "status": e.status}


def publish_exam(db: Session, exam_id: int, scope: set[int] | None = None, bg=None) -> dict:
    e = db.get(ExamDefinition, exam_id)
    if not e:
        raise DomainError(status.HTTP_404_NOT_FOUND, "考试不存在")
    _check_exam_scope(db, e, scope)
    # 发布前固化卷面并校验非空：否则可发布一张 0 题试卷，考生交白卷即得 total_score=0
    from app.services.exam.sessions import _ensure_exam_questions

    if not _ensure_exam_questions(db, e):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "该考试没有可用题目，无法发布；请先配置组卷来源")
    e.status = "published"
    db.commit()
    db.refresh(e)
    if bg is not None:
        # 只取邮箱列（不实例化 ORM 对象），并收敛为**一个**后台任务：
        # 原实现把全部活跃用户物化后逐人挂任务，任务数与内存随人数线性增长。
        email_stmt = select(User.email).where(User.status == "active")
        if e.group_ids:
            # 与可见性同口径：指派到父分组须覆盖其子分组成员，否则漏发通知
            expanded = _expand_groups(db, e.group_ids)
            email_stmt = (
                email_stmt.outerjoin(UserGroup, UserGroup.user_id == User.id)
                .where(or_(UserGroup.group_id.in_(expanded), User.dept_group_id.in_(expanded)))
                .distinct()
            )
        recipients = [row[0] for row in db.execute(email_stmt).all() if row[0]]
        if recipients:
            settings = get_settings(db)
            bg.add_task(
                mail_service.send_safely,
                mail_service.send_exam_publish_many,
                settings,
                recipients,
                e.name,
                e.end_at or "考试结束前",
            )
    return {"id": e.id, "status": e.status}
