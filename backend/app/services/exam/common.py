"""考试域公共工具：时间窗口/超时判定、可见性与考试摘要。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import subtree_map
from app.core.errors import DomainError
from app.core.timeutil import business_tz
from app.core.timeutil import utcnow_iso as _now
from app.models.exam import ExamDefinition, ExamQuestion, PaperTemplate
from app.models.record import ExamSession

logger = logging.getLogger("quizhub")

# 供 exam_service 门面与 sessions/scoring 复用（含时间戳与尝试次数工具）
__all__ = [
    "_now",
    "_parse_time",
    "_within_time_window",
    "_is_overtime",
    "_count_attempts",
    "_attempt_counts",
    "_exam_brief",
    "_exam_question_count",
    "_exam_question_counts",
    "_expand_groups",
    "_user_can_access_exam",
    "exam_in_scope",
]


def _expand_groups(db: Session, group_ids) -> set[int]:
    """把分组 id 展开为「自身 + 全部后代」。

    考试指派到父分组（如「研发部」）时必须覆盖其子分组（如「研发部/一班」）的成员，
    否则把考试指派给部门对部门下挂在子分组的人不生效——这与部门管理员数据范围
    （部门 + 子分组）、以及文档口径都不一致。可见性与发布通知共用本函数。
    """
    mapping = subtree_map(db)
    out: set[int] = set()
    for gid in group_ids or []:
        out |= mapping.get(gid, {gid})
    return out


def _user_can_access_exam(e: ExamDefinition, user_group_ids: set[int], subtree: dict[int, set[int]]) -> bool:
    """考试指派分组（展开子树后）与用户分组有交集（空指派表示不限）。

    subtree 由调用方用 `core.deps.subtree_map(db)` 一次性取出后传入，
    避免在列表循环中对每个考试各查一次分组树。
    """
    e_groups = e.group_ids or []
    if not e_groups:
        return True
    allowed: set[int] = set()
    for gid in e_groups:
        allowed |= subtree.get(gid, {gid})
    return bool(allowed.intersection(user_group_ids))


def exam_in_scope(group_ids: list[int] | None, scope: set[int] | None) -> bool:
    """考试是否落在调用者的数据范围内（管理端口径）。

    口径是**子集**而非「有交集」：只要有一个指派分组在调用者范围之外，就说明该考试
    跨出了其管辖范围（例如共同指派给两个部门的考试，两个部门管理员都无权改）。
    无指派分组（全员可见）的考试不属于任何部门管理员的可操作范围，故 scope 非 None
    时返回 False。

    概览计数、考试列表、操作前校验必须共用本函数：三处口径一旦分叉，就会出现
    「概览里算得到、列表里看不到、编辑时 403」的自相矛盾。

    Args:
        group_ids: 考试的指派分组（None/空表示未指派）。
        scope: 调用者的数据范围；None（super_admin）表示全量。

    Returns:
        是否在范围内。
    """
    if scope is None:
        return True
    if not group_ids:
        return False
    return set(group_ids).issubset(scope)


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "考试时间配置无效") from None
    if parsed.tzinfo is None:
        # 无偏移值来自管理端日期选择器（value-format 不带时区），语义是**业务本地时间**。
        # 原实现按 UTC 解析，Asia/Shanghai 部署下考试时段整体偏移 8 小时。
        parsed = parsed.replace(tzinfo=business_tz())
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
    """按服务端开始时间和考试截止时间判断是否超时。

    时间字段无法解析（历史脏数据）时记 WARNING 并按「未超时」处理，而不是抛错阻断交卷——
    与 `sessions._remaining_seconds` 的降级口径一致；否则一条坏数据会让考生永久无法交卷。
    """
    now = now or datetime.now(timezone.utc)
    try:
        deadline = _parse_time(sess.started_at) + timedelta(minutes=e.duration_min)
        if e.end_at:
            deadline = min(deadline, _parse_time(e.end_at))
    except DomainError:
        logger.warning("[exam] 会话时间字段无法解析，已按未超时处理：session=%s", sess.id)
        return False
    return now >= deadline


_ATTEMPT_TERMINAL_STATUSES = ("submitted", "scored", "reviewed")


def _count_attempts(db: Session, exam_id: int, user_id: int) -> int:
    # scoring 视为结算中、不确定是否计入尝试次数；仅计已确认结束的终态，
    # 避免崩溃残留的 scoring 会话把用户尝试次数永久占满。
    return int(
        db.execute(
            select(func.count())
            .select_from(ExamSession)
            .where(
                ExamSession.exam_definition_id == exam_id,
                ExamSession.user_id == user_id,
                ExamSession.status.in_(_ATTEMPT_TERMINAL_STATUSES),
            )
        ).scalar_one()
    )


def _attempt_counts(db: Session, exam_ids: list[int], user_id: int) -> dict[int, int]:
    """批量统计某用户对多场考试的已确认尝试次数。

    列表页此前对每场可见考试各调用一次 `_count_attempts`（N 场 = N 次查询），
    这里改为一次 GROUP BY 取回，消除 N+1。
    """
    ids = list(exam_ids)
    if not ids:
        return {}
    return {
        r[0]: int(r[1])
        for r in db.execute(
            select(ExamSession.exam_definition_id, func.count())
            .where(
                ExamSession.exam_definition_id.in_(ids),
                ExamSession.user_id == user_id,
                ExamSession.status.in_(_ATTEMPT_TERMINAL_STATUSES),
            )
            .group_by(ExamSession.exam_definition_id)
        ).all()
    }


def _exam_brief(
    e: ExamDefinition,
    state: str,
    attempts: int = 0,
    db: Session | None = None,
    total_questions: int | None = None,
) -> dict:
    """用户端考试摘要。

    刻意不包含 rules / paper_template_id / group_ids：这些是组卷配置与指派范围，
    属于管理端信息，不应暴露给参加考试的用户。

    total_questions 可由调用方用 `_exam_question_counts` 批量算好后传入（列表页），
    避免逐场 COUNT 的 N+1。
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
        "total_questions": _exam_question_count(e, db) if total_questions is None else total_questions,
    }


def _exam_question_count(e: ExamDefinition, db: Session | None = None) -> int:
    """考试题目数的展示口径：手选 > 已固化 > 模板 > 规则配额。"""
    if e.manual_questions:
        return len(e.manual_questions)
    if db is not None:
        frozen = db.execute(
            select(func.count()).select_from(ExamQuestion).where(ExamQuestion.exam_definition_id == e.id)
        ).scalar_one()
        if frozen:
            return int(frozen)
        if e.paper_template_id:
            tpl = db.get(PaperTemplate, e.paper_template_id)
            if tpl:
                if tpl.question_ids:
                    return len(tpl.question_ids)
                quota = (tpl.config or {}).get("type_quota") or {}
                return sum(int(v) for v in quota.values())
    rules = e.rules or {}
    quota = rules.get("type_quota") or {}
    return sum(int(v) for v in quota.values())


def _exam_question_counts(db: Session, exams: list[ExamDefinition]) -> dict[int, int]:
    """批量计算多场考试的展示题数，消除列表页「每场一次 COUNT」的 N+1。

    与 `_exam_question_count` 同口径（手选 > 已固化 > 模板 > 规则配额），
    但把已固化题数压成一次 GROUP BY、模板压成一次 IN 查询。

    Args:
        db: 数据库会话。
        exams: 需要计算题数的考试列表。

    Returns:
        考试 id → 展示题数。
    """
    if not exams:
        return {}
    counts: dict[int, int] = {}
    need_frozen: list[ExamDefinition] = []
    tpl_ids: set[int] = set()
    for e in exams:
        if e.manual_questions:
            counts[e.id] = len(e.manual_questions)
            continue
        need_frozen.append(e)
        if e.paper_template_id:
            tpl_ids.add(e.paper_template_id)

    frozen: dict[int, int] = {}
    if need_frozen:
        frozen = {
            row[0]: int(row[1])
            for row in db.execute(
                select(ExamQuestion.exam_definition_id, func.count())
                .where(ExamQuestion.exam_definition_id.in_([e.id for e in need_frozen]))
                .group_by(ExamQuestion.exam_definition_id)
            ).all()
        }
    templates: dict[int, PaperTemplate] = {}
    if tpl_ids:
        templates = {
            tpl.id: tpl
            for tpl in db.execute(select(PaperTemplate).where(PaperTemplate.id.in_(tpl_ids))).scalars().all()
        }
    for e in need_frozen:
        if frozen.get(e.id):
            counts[e.id] = frozen[e.id]
            continue
        tpl = templates.get(e.paper_template_id) if e.paper_template_id else None
        if tpl is not None:
            if tpl.question_ids:
                counts[e.id] = len(tpl.question_ids)
            else:
                quota = (tpl.config or {}).get("type_quota") or {}
                counts[e.id] = sum(int(v) for v in quota.values())
            continue
        quota = (e.rules or {}).get("type_quota") or {}
        counts[e.id] = sum(int(v) for v in quota.values())
    return counts
