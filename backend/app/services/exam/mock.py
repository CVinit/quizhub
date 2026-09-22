"""模拟考试：题库范围收敛、题型配额分配、组卷预览与自助开考。"""

from __future__ import annotations

import json

from fastapi import status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.question import Question, QuestionBank
from app.models.record import ExamSession
from app.models.user import User
from app.services.exam.sessions import start_exam
from app.services.paper_service import generate_paper
from app.services.system_service import get_settings

# ---------- 模拟考试（完全用户自助：题库/题量/题型比例均由用户开考前设置）----------
# 用户可选题量的档位与硬上限。硬上限是安全边界（防止超大 size 拖垮组卷与落库），
# 不是业务配置，故用常量而非后台设置项。
MOCK_SIZE_PRESETS = (10, 20, 30, 50, 100)
MOCK_DEFAULT_SIZE = 30
MOCK_MAX_QUESTIONS = 100
MOCK_ALLOCATIONS = ("auto", "manual")


def _normalize_bank_ids(bank_ids) -> list[int]:
    """规范化并去重题库 id（保持稳定顺序，供复用键比较）。"""
    out: list[int] = []
    for bid in bank_ids or []:
        if isinstance(bid, bool) or not isinstance(bid, int):
            raise DomainError(status.HTTP_400_BAD_REQUEST, "题库 id 必须是整数")
        if bid not in out:
            out.append(bid)
    return sorted(out)


def resolve_mock_scope(db: Session, requested_bank_ids) -> list[int]:
    """把用户请求的题库范围收敛到「允许用户练习」的题库范围内。

    模拟考试在产品上是练习性质（docs/requirement.md：练习性，不计正式档案），
    管理员把题库标注为「仅考试使用」（practice_enabled=False）后，用户不得通过
    模拟考试练到它，否则该开关形同虚设。这是本模块的第一道红线。

    规则：请求的 id 与开放题库取交集（非法 id 静默剔除，避免探测题库状态）；
    交集为空（未选或全部非法）时回退为全部开放题库。
    """
    from app.services.practice_service import enabled_bank_ids

    enabled = enabled_bank_ids(db)
    requested = _normalize_bank_ids(requested_bank_ids)
    if not requested:
        return sorted(enabled)
    allowed = [bid for bid in requested if bid in set(enabled)]
    return allowed or sorted(enabled)


def mock_banks(db: Session) -> dict:
    """用户端模拟考试可选题库（只含开放练习的题库及其各题型题量）。

    刻意不返回 practice_enabled 字段：用户端不需要知道"有哪些题库被关闭了"，
    避免泄露题库存在性与题量。
    """

    from app.services.practice_service import enabled_bank_ids
    from app.services.question_service import QUESTION_TYPES

    enabled = set(enabled_bank_ids(db))
    if not enabled:
        return {
            "banks": [],
            "total_questions": 0,
            "size_presets": list(MOCK_SIZE_PRESETS),
            "default_size": MOCK_DEFAULT_SIZE,
            "max_questions": MOCK_MAX_QUESTIONS,
        }

    rows = (
        db.execute(select(QuestionBank).where(QuestionBank.id.in_(enabled)).order_by(QuestionBank.name)).scalars().all()
    )
    # 一次 GROUP BY 取各题库各题型题量，避免逐库统计
    stat_rows = db.execute(
        select(Question.bank_id, Question.type, func.count(Question.id))
        .where(Question.bank_id.in_(enabled))
        .group_by(Question.bank_id, Question.type)
    ).all()
    by_bank: dict[int, dict[str, int]] = {}
    for bid, qtype, cnt in stat_rows:
        if qtype in QUESTION_TYPES:
            by_bank.setdefault(bid, {})[qtype] = cnt

    banks = []
    total = 0
    for b in rows:
        per_type = {t: by_bank.get(b.id, {}).get(t, 0) for t in QUESTION_TYPES}
        cnt = sum(per_type.values())
        total += cnt
        banks.append({"id": b.id, "name": b.name, "question_count": cnt, "type_stats": per_type})
    # 题干为空的题库对组卷无用，但保留展示（用户可看到题量 0）
    return {
        "banks": banks,
        "total_questions": total,
        "size_presets": list(MOCK_SIZE_PRESETS),
        "default_size": MOCK_DEFAULT_SIZE,
        "max_questions": MOCK_MAX_QUESTIONS,
    }


def _mock_avail_by_type(db: Session, bank_ids: list[int]) -> dict[str, int]:
    """范围内各题型可用题量。"""
    from app.services.question_service import QUESTION_TYPES, type_stats

    stats = type_stats(db, bank_ids=list(bank_ids))
    return {t: int(stats.get(t, 0)) for t in QUESTION_TYPES}


def build_mock_spec(db: Session, bank_ids, size, type_quota=None, allocation="auto", objective_only=False):
    """构造模拟考试规则：收敛范围 → 校验题量 → 计算题型配额。

    供 /mock/preview（预览）与 /exams/mock/start（实际开考）共用，确保两侧口径
    完全一致——预览说能出 30 题，开考就必须是 30 题。

    Returns:
        (rules, meta)：rules 可直接写入 ExamDefinition.rules；
        meta 含 avail_by_type / avail_total / size_downgraded 等，供前端提示。
    """
    from app.services.paper_service import allocate_quota

    scope = resolve_mock_scope(db, bank_ids)
    avail = _mock_avail_by_type(db, scope)
    avail_total = sum(avail.values())
    if avail_total <= 0:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "所选范围内没有可用题目")

    # 题量校验与下调：范围内题量不足时把目标题量下调到可满足的最大值，
    # 否则用户无论怎么填题型都无法满足「Σ手填 == 题量」（见设计文档 §9）。
    # 缺省值收在服务层，保证 /mock/preview 与 /mock/start 对"未传 size"口径一致
    # （原实现 preview 直接把 None 传进来 → 400，而 start 用默认 30，两侧行为不一致）。
    if size is None:
        size = MOCK_DEFAULT_SIZE
    if isinstance(size, bool) or not isinstance(size, int):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "题量必须是整数")
    if size <= 0:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "题量必须大于 0")
    if size > MOCK_MAX_QUESTIONS:
        raise DomainError(status.HTTP_400_BAD_REQUEST, f"题量不能超过 {MOCK_MAX_QUESTIONS} 题")
    effective_size = size
    downgraded = False
    if effective_size > avail_total:
        effective_size = avail_total
        downgraded = True

    manual = dict(type_quota) if type_quota else None
    # 「仅客观题」：清零简答题；自动分配下由权重自然归零，手填下需改写用户输入
    if objective_only and manual:
        manual.pop("简答题", None)
        manual = manual or None
        if manual is None:
            raise DomainError(status.HTTP_400_BAD_REQUEST, "勾选仅客观题后没有可出的题型")
    if allocation not in MOCK_ALLOCATIONS:
        raise DomainError(status.HTTP_400_BAD_REQUEST, f"题型分配方式必须是 {MOCK_ALLOCATIONS} 之一")
    if manual and allocation == "auto":
        # 用户手填了配额 → 视为手动模式，避免"传了配额却被忽略"
        allocation = "manual"

    usable = dict(avail)
    if objective_only and not manual:
        usable["简答题"] = 0
    if manual:
        # 手填路径由 manual_quota 非空触发，allocation 参数此时不参与策略选择
        quota = allocate_quota(avail, effective_size, manual_quota=manual)
    else:
        quota = allocate_quota(usable, effective_size)

    rules = {
        "bank_ids": scope,
        "requested_size": size,
        "type_quota": quota,
        "allocation": allocation,
        "objective_only": bool(objective_only),
        "max_questions": MOCK_MAX_QUESTIONS,
    }
    meta = {
        "avail_by_type": avail,
        "avail_total": avail_total,
        "effective_size": effective_size,
        "size_downgraded": downgraded,
    }
    return rules, meta


def preview_mock_paper(db: Session, bank_ids, size, type_quota=None, allocation="auto", objective_only=False) -> dict:
    """预览模拟考试组卷结果（不落库、不建会话）。"""
    rules, meta = build_mock_spec(db, bank_ids, size, type_quota, allocation, objective_only)
    paper = generate_paper(db, rules)
    return {
        "count": paper["count"],
        "total_score": paper["total_score"],
        "type_dist": rules["type_quota"],
        "avail_by_type": meta["avail_by_type"],
        "avail_total": meta["avail_total"],
        "effective_size": meta["effective_size"],
        "size_downgraded": meta["size_downgraded"],
    }


def _mock_rules_key(rules: dict) -> str:
    """模拟考试定义的复用键：题库范围 + 题量 + 题型配额 + 分配方式。

    任一维度变化都视为"用户想要一份不同的卷子"，必须新建定义；否则用户换了题库
    或改了题量却拿回旧定义的固化试卷（题量/题型与设置不符）。
    """
    return json.dumps(
        {
            "bank_ids": sorted(rules.get("bank_ids") or []),
            "requested_size": rules.get("requested_size"),
            "type_quota": rules.get("type_quota") or {},
            "allocation": rules.get("allocation"),
            "objective_only": bool(rules.get("objective_only")),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _cleanup_stale_mock_defs(db: Session, user_id: int, keep_id: int | None = None) -> int:
    """清理该用户无任何作答记录的 ongoing mock 定义，避免 exam_definitions 无界堆积。

    仅清理「没有 ExamSession」的定义：有会话的定义保留（可能是用户未完成的作答）。
    """

    rows = (
        db.execute(
            select(ExamDefinition).where(
                ExamDefinition.type == "mock",
                ExamDefinition.created_by == user_id,
                ExamDefinition.status == "ongoing",
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0
    ids = [e.id for e in rows if e.id != keep_id]
    if not ids:
        return 0
    with_session = {
        r[0]
        for r in db.execute(select(ExamSession.exam_definition_id).where(ExamSession.exam_definition_id.in_(ids))).all()
    }
    removed = 0
    for e in rows:
        if e.id in ids and e.id not in with_session:
            # 先删题目固化行，再删定义（exam_questions 外键为级联，但显式删除更明确）
            db.execute(delete(ExamQuestion).where(ExamQuestion.exam_definition_id == e.id))
            db.delete(e)
            removed += 1
    if removed:
        db.commit()
    return removed


def start_mock_exam(
    db: Session,
    user: User,
    bank_ids=None,
    size: int | None = None,
    type_quota: dict | None = None,
    allocation: str = "auto",
    objective_only: bool = False,
    show_analysis: bool = True,
) -> dict:
    """模拟考试：按用户开考前设置即时组卷并开考。

    完全用户自助——题库范围、题量、题型比例均由用户在设置对话框内指定；
    后台不再提供模拟考试配置。所有入参在服务端重新校验（不信任前端）。
    """
    settings = get_settings(db, "exam")
    rules, _meta = build_mock_spec(
        db,
        bank_ids,
        size,  # None 由 build_mock_spec 收敛为 MOCK_DEFAULT_SIZE
        type_quota,
        allocation,
        objective_only,
    )
    rules["show_analysis"] = bool(show_analysis)

    key = _mock_rules_key(rules)
    # 复用同一用户、同一「设置组合」的进行中定义，避免每开考一次就新增一行
    # exam_definitions + N 行 exam_questions 导致数据无界增长。
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
        .all()
    )
    for e in existing:
        if _mock_rules_key(e.rules or {}) == key:
            # 范围与配额以本次设置为准（管理员后来关闭某题库时，复用中的定义也须收敛）
            e.rules = rules
            db.commit()
            _cleanup_stale_mock_defs(db, user.id, keep_id=e.id)
            return start_exam(db, user, e.id)

    e = ExamDefinition(
        name="模拟考试",
        type="mock",
        rules=rules,
        group_ids=None,
        start_at=None,
        end_at=None,
        duration_min=int(settings.get("default_exam_duration_min", "90")),
        pass_score=float(settings.get("default_pass_score", "60")),
        max_attempts=0,
        show_score_immediately=True,
        show_analysis=bool(show_analysis),
        need_review=False,
        status="ongoing",
        created_by=user.id,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    _cleanup_stale_mock_defs(db, user.id, keep_id=e.id)
    return start_exam(db, user, e.id)
