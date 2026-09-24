"""题库业务：题库来源、题目 CRUD、标签自动维护。"""

from __future__ import annotations

import logging

from sqlalchemy import delete, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import DomainError
from app.core.like import ESCAPE_CHAR, like_pattern
from app.core.status import BAD_REQUEST, CONFLICT, FORBIDDEN, NOT_FOUND
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.group import Group
from app.models.question import QUESTION_TYPE, Question, QuestionBank, QuestionTag
from app.models.record import PracticeRecord, QuestionState
from app.schemas.question import (
    QuestionBankCreate,
    QuestionBankUpdate,
    QuestionCreate,
    QuestionUpdate,
)
from app.utils.question_text import count_blanks

logger = logging.getLogger("quizhub")

# 单一来源：直接复用模型层常量，避免题型白名单在模型/服务两处漂移
# （历史上曾有三份副本，见 user_service 中 ROLES/STATUSES 的同类治理）。
QUESTION_TYPES = QUESTION_TYPE

# update_question 允许客户端修改的字段白名单。
# 与 QuestionUpdate schema 的字段保持一致，但作为服务层的独立防线：
# 任何未列入此处的键都会被拒绝，避免 schema 演进时出现静默的批量赋值漏洞。
QUESTION_UPDATABLE_FIELDS = frozenset(
    {
        "type",
        "question",
        "options",
        "left_items",
        "right_items",
        "answer",
        "analysis",
        "difficulty",
        "tags",
        "score",
        "group_id",
        "bank_id",
    }
)


def _validate_group(db: Session, group_id: int | None, scope: set[int] | None) -> None:
    if group_id is not None and not db.get(Group, group_id):
        raise DomainError(BAD_REQUEST, "分组不存在")
    if scope is not None and (group_id is None or group_id not in scope):
        raise DomainError(FORBIDDEN, "题目必须归属在可管理分组内")


def _get_allowed_bank(
    db: Session,
    bank_id: int | None,
    scope: set[int] | None,
    *,
    missing_status: int = BAD_REQUEST,
) -> QuestionBank | None:
    """按 id 取题库并做数据范围校验。

    Args:
        db: 数据库会话。
        bank_id: 题库 id；None 表示「未指定题库」，直接返回 None。
        scope: 调用者数据范围；None（super_admin）表示全量。
        missing_status: 题库不存在时使用的状态码。**路径参数**来源（`PUT`/`DELETE`
            `/admin/question-banks/{id}`）传 `NOT_FOUND`（404）；**请求体**引用
            （创建/更新题目时指定 `bank_id`）保持 `BAD_REQUEST`（400）——
            「目标资源不存在」与「请求体非法」是两回事。

    Returns:
        题库对象；`bank_id` 为 None 时返回 None。

    Raises:
        DomainError: `missing_status`（题库不存在）或 403（无权操作该题库）。
    """
    if bank_id is None:
        return None
    bank = db.get(QuestionBank, bank_id)
    if not bank:
        raise DomainError(missing_status, "题库不存在")
    if scope is not None and (bank.group_id is None or bank.group_id not in scope):
        raise DomainError(FORBIDDEN, "无权操作该题库")
    return bank


def _validate_answer_shape(
    qtype: str,
    answer: object,
    options: list | None = None,
    question_text: str | None = None,
    *,
    left_items: list | None = None,
    right_items: list | None = None,
) -> None:
    """按题型校验答案形状，拒绝空答案与「永远判错」的答案。

    - 单选：非空单字符字母，必须落在选项集范围内；选项必须非空
    - 多选：非空字母串（不重复），必须落在选项集范围内；选项必须非空
    - 判断：'正确' 或 '错误'
    - 填空：非空 list，每个空为非空 list[str]，且空位数与题干空位数一致
    - 简答：非空字符串
    - 拖拽：非空 dict，且映射与 left_items/right_items 一一对应

    与 Excel 导入路径（`utils/excel._parse_row`）保持同一口径：这两条路径此前只在一侧校验，
    另一侧能存下「任何作答都判错」的题（空位数不一致、选项为空、答案越界、拖拽映射与
    左右项分叉），已逐条实跑复现。

    Args:
        qtype: 题型（QUESTION_TYPES 之一）。
        answer: 答案（多态，见各分支）。
        options: 选择题的选项列表；选择题必须非空，且答案字母须在范围内。
        question_text: 题干；填空题据此交叉校验空位数（None 表示不校验）。
        left_items: 拖拽题左项（题项）。
        right_items: 拖拽题右项（容器）。

    Raises:
        DomainError: 400，答案形状非法、超出选项范围或与题干/左右项不一致。
    """
    if qtype in ("单选题", "多选题"):
        if not isinstance(answer, str) or not answer.strip():
            raise DomainError(BAD_REQUEST, "选择题答案不能为空")
        # 单选只允许一个字母：`grade()` 对单选做整串相等比较，接受 "AB"/"Z" 会存下
        # 永远无法作答（或与选项集不符）的题目。Excel 导入路径本就按选项集校验，
        # 两条创建路径必须同口径。
        letters = answer.strip().upper()
        if qtype == "单选题" and len(letters) != 1:
            raise DomainError(BAD_REQUEST, "单选题答案必须是一个选项字母（如 A）")
        if not letters.isalpha() or not letters.isascii():
            raise DomainError(BAD_REQUEST, "选择题答案必须是选项字母（如 A 或 ABC）")
        # 重复字母（如 "AA"）经 grade() 排序比较后与任何作答都不相等 → 题目永远判错
        if len(set(letters)) != len(letters):
            raise DomainError(BAD_REQUEST, "选择题答案不能包含重复字母")
        # 选项是选择题可作答的前提：空/缺失时前端没有可点选项，任何作答都判错。
        # Excel 导入路径要求选项非空，手工路径此前用 `if isinstance(options, list) and options:`
        # 跳过校验，于是 options=[] + answer="A" 能入库。
        if not isinstance(options, list) or not options:
            raise DomainError(BAD_REQUEST, "选择题必须提供选项")
        valid_letters = {chr(ord("A") + i) for i in range(len(options))}
        out_of_range = sorted(set(letters) - valid_letters)
        if out_of_range:
            raise DomainError(
                BAD_REQUEST,
                f"答案 {'/'.join(out_of_range)} 超出选项范围（共 {len(options)} 个选项，最大 {max(valid_letters)}）",
            )
    elif qtype == "判断题":
        if answer not in ("正确", "错误"):
            raise DomainError(BAD_REQUEST, "判断题答案必须是 正确/错误")
    elif qtype == "填空题":
        if not isinstance(answer, list) or not answer:
            raise DomainError(BAD_REQUEST, "填空题答案不能为空")
        for blanks in answer:
            if not isinstance(blanks, list) or not blanks:
                raise DomainError(BAD_REQUEST, "填空题每空至少需要一个等价答案")
        # 空位数必须与题干一致：判分要求 len(correct_answer) == len(user_answer)，而前端按题干里
        # 连续下划线数量渲染输入框。少写一个空位即存下一道永远判错的题（Excel 路径已拦，手工路径此前放行）。
        if question_text is not None:
            expected_blanks = count_blanks(question_text)
            if len(answer) != expected_blanks:
                raise DomainError(
                    BAD_REQUEST,
                    f"答案空位数({len(answer)})与题干空位数({expected_blanks})不一致",
                )
    elif qtype == "简答题":
        if not isinstance(answer, str) or not answer.strip():
            raise DomainError(BAD_REQUEST, "简答题参考答案不能为空")
    elif qtype == "拖拽题":
        if not isinstance(answer, dict) or not answer:
            raise DomainError(BAD_REQUEST, "拖拽题答案映射不能为空")
        # 拖拽题由左项（题项）拖入右项（容器）作答，判分按映射逐对比较且要求数量相等。
        # 左项没有对应容器（或容器不在右项里）时，考生无论怎么拖都对不上 → 题目不可作答。
        # Excel 路径由答案反向构造左右项，天然一致；手工路径此前完全不校验。
        items = left_items if isinstance(left_items, list) else []
        containers = right_items if isinstance(right_items, list) else []
        if not items or not containers or not all(isinstance(i, str) for i in [*items, *containers]):
            raise DomainError(BAD_REQUEST, "拖拽题必须提供左项与右项（题项与容器）")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in answer.items()):
            raise DomainError(BAD_REQUEST, "拖拽题答案映射必须是「题项:容器」文本对")
        if set(answer) != set(items) or not set(answer.values()).issubset(set(containers)):
            raise DomainError(BAD_REQUEST, "拖拽题答案映射必须与左项/右项一一对应")


def _count_exams_referencing_questions(db: Session, qids: list[int]) -> int:
    """统计「手工选题」中引用了这些题目的考试数（ExamDefinition.manual_questions）。

    删题守卫原先只统计 `exam_questions`（已固化的卷面），而手工选题的考试在**首次开考/
    发布前**没有任何固化行，于是管理员能删掉它引用的题目：`manual_questions` 中残留不存在
    的 id，该考试从此永久无法开考与发布（`_persist_exam_questions` 抛「考试包含不存在的
    题目」），而管理端列表仍按 `len(manual_questions)` 显示题数（虚高）。

    用 SQLite JSON1 的 json_each 做元素级匹配，避免把考试表全量载入 Python。
    """
    if not qids:
        return 0
    json_values = func.json_each(ExamDefinition.manual_questions).table_valued("value")
    return int(
        db.execute(
            select(func.count())
            .select_from(ExamDefinition)
            .where(
                ExamDefinition.manual_questions.is_not(None),
                exists(select(1).select_from(json_values).where(json_values.c.value.in_(qids))),
            )
        ).scalar_one()
    )


# ---------- 题库来源 ----------
def list_banks(db: Session, scope: set[int] | None = None, practice_enabled: bool | None = None) -> list[dict]:
    """返回题库，并附带各题库题目数（一次 GROUP BY 聚合，避免逐套 COUNT）。

    practice_enabled 为 None 时返回全部题库（管理端默认：需看到并管理已关闭的题库）；
    显式传 True/False 时按「开放练习 / 仅考试使用」筛选。
    """
    stmt = select(QuestionBank)
    if scope is not None:
        stmt = stmt.where(QuestionBank.group_id.in_(scope))
    if practice_enabled is not None:
        stmt = stmt.where(QuestionBank.practice_enabled.is_(practice_enabled))
    rows = db.execute(stmt.order_by(QuestionBank.id.desc())).scalars().all()
    if not rows:
        return []
    bank_ids = [b.id for b in rows]
    cnt_rows = db.execute(
        select(Question.bank_id, func.count(Question.id))
        .where(Question.bank_id.in_(bank_ids))
        .group_by(Question.bank_id)
    ).all()
    cnt_map = {r[0]: r[1] for r in cnt_rows}
    return [
        {
            "id": b.id,
            "name": b.name,
            "group_id": b.group_id,
            "question_count": cnt_map.get(b.id, 0),
            "practice_enabled": bool(b.practice_enabled),
        }
        for b in rows
    ]


def create_bank(db: Session, payload: QuestionBankCreate, scope: set[int] | None = None) -> QuestionBank:
    _validate_group(db, payload.group_id, scope)
    b = QuestionBank(name=payload.name, group_id=payload.group_id, practice_enabled=payload.practice_enabled)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def update_bank(db: Session, bank_id: int, payload: QuestionBankUpdate, scope: set[int] | None = None) -> QuestionBank:
    """更新题库（改名 / 练习开关）。练习开关只影响后续练习入口，历史记录保留。

    `bank_id` 来自路径参数，不存在时按 404 语义返回（原先 helper 统一抛 400，
    紧随其后的 `if b is None: raise 404` 因此永不可达 —— 意图与实现不一致）。
    """
    b = _get_allowed_bank(db, bank_id, scope, missing_status=NOT_FOUND)
    if b is None:  # 仅为类型收敛：bank_id 非 None，helper 已对不存在的 id 抛 404
        raise DomainError(NOT_FOUND, "题库不存在")
    if payload.name is not None:
        b.name = payload.name
    if payload.practice_enabled is not None:
        b.practice_enabled = payload.practice_enabled
    db.commit()
    db.refresh(b)
    return b


def delete_bank(db: Session, bank_id: int, scope: set[int] | None = None) -> None:
    """删除题库及其题目。

    若其中任一题目已被考试引用（exam_questions 或手工选题），拒绝删除以保持历史考试可追溯；
    此时管理员可改用「关闭练习」。`bank_id` 来自路径参数，不存在时 404。
    """
    b = _get_allowed_bank(db, bank_id, scope, missing_status=NOT_FOUND)
    if b is None:  # 仅为类型收敛：bank_id 非 None，helper 已对不存在的 id 抛 404
        raise DomainError(NOT_FOUND, "题库不存在")
    qids = [r[0] for r in db.execute(select(Question.id).where(Question.bank_id == bank_id)).all()]
    if qids:
        used = db.execute(
            select(func.count()).select_from(ExamQuestion).where(ExamQuestion.question_id.in_(qids))
        ).scalar_one()
        manual = _count_exams_referencing_questions(db, qids)
        if used or manual:
            raise DomainError(
                CONFLICT,
                f"该题库有 {used} 道题被考试卷面引用、{manual} 场考试的手工选题引用，无法删除；可改为关闭练习",
            )
        # 先取回被删作答的时间戳，再清理引用这些题目的练习状态，避免留下孤儿数据。
        # stats_user_daily 是 practice_records 的物化聚合，删除源行后必须重算对应日期，
        # 否则排行/面板会永久保留已删除的作答（startup_refresh 只覆盖今昨两天，
        # refresh_recent 上限 60 天，历史行没有其它修正路径）。
        stamps = [
            r[0]
            for r in db.execute(select(PracticeRecord.answered_at).where(PracticeRecord.question_id.in_(qids))).all()
        ]
        db.execute(delete(PracticeRecord).where(PracticeRecord.question_id.in_(qids)))
        db.execute(delete(QuestionState).where(QuestionState.question_id.in_(qids)))
        db.execute(delete(Question).where(Question.id.in_(qids)))
    else:
        stamps = []
    db.delete(b)
    db.commit()
    _refresh_stats_after_delete(db, stamps)


# ---------- 题目 CRUD ----------
def list_questions(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    type_: str | None = None,
    bank_id: int | None = None,
    group_id: int | None = None,
    difficulty: int | None = None,
    keyword: str | None = None,
    scope: set[int] | None = None,
) -> tuple[list[Question], int]:
    stmt = select(Question)
    if scope is not None:
        stmt = stmt.where(Question.group_id.in_(scope))
    if type_:
        stmt = stmt.where(Question.type == type_)
    if bank_id:
        stmt = stmt.where(Question.bank_id == bank_id)
    if group_id:
        stmt = stmt.where(Question.group_id == group_id)
    if difficulty is not None:
        stmt = stmt.where(Question.difficulty == difficulty)
    if keyword:
        stmt = stmt.where(Question.question.like(like_pattern(keyword), escape=ESCAPE_CHAR))
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar() or 0
    rows = db.execute(stmt.order_by(Question.id.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return list(rows), total


def _ensure_tags(db: Session, tags: list[str]) -> None:
    """确保标签存在。用 SAVEPOINT 包裹单条插入，避免回滚波及整个事务。"""
    for name in tags:
        if not name:
            continue
        exists = db.execute(select(QuestionTag).where(QuestionTag.name == name)).scalar_one_or_none()
        if not exists:
            try:
                with db.begin_nested():
                    db.add(QuestionTag(name=name))
            except IntegrityError:
                # 并发或重复插入触发唯一约束冲突；SAVEPOINT 已回滚，主事务不受影响
                pass


def create_question(db: Session, payload: QuestionCreate, scope: set[int] | None = None) -> Question:
    if payload.type not in QUESTION_TYPES:
        raise DomainError(BAD_REQUEST, f"题型必须是 {QUESTION_TYPES} 之一")
    _validate_answer_shape(
        payload.type,
        payload.answer,
        payload.options,
        payload.question,
        left_items=payload.left_items,
        right_items=payload.right_items,
    )
    _validate_group(db, payload.group_id, scope)
    bank = _get_allowed_bank(db, payload.bank_id, scope)
    if bank and bank.group_id is not None and bank.group_id != payload.group_id:
        raise DomainError(BAD_REQUEST, "题目分组必须与题库分组一致")
    if payload.tags:
        _ensure_tags(db, payload.tags)
    q = Question(
        bank_id=payload.bank_id,
        type=payload.type,
        question=payload.question,
        options=payload.options,
        left_items=payload.left_items,
        right_items=payload.right_items,
        answer=payload.answer,
        analysis=payload.analysis,
        difficulty=payload.difficulty,
        tags=payload.tags,
        score=payload.score,
        group_id=payload.group_id,
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    return q


def update_question(db: Session, qid: int, payload: QuestionUpdate, scope: set[int] | None = None) -> Question:
    q = db.get(Question, qid)
    if not q:
        raise DomainError(NOT_FOUND, "题目不存在")
    _validate_group(db, q.group_id, scope)
    data = payload.model_dump(exclude_unset=True)
    if "type" in data and data["type"] not in QUESTION_TYPES:
        raise DomainError(BAD_REQUEST, f"题型必须是 {QUESTION_TYPES} 之一")
    # 只要「本次生效的题型/选项/题干/答案/左右项」任一变化，就按生效后的组合重跑一次形状校验。
    # 原实现只在「单独改题型」或「显式传 answer」时校验，于是「只改选项」或「只改题干」可以
    # 绕过校验，落库一道永远判错的题（已实跑复现：3 选项单选把选项缩成 2 个、答案 C 仍越界）。
    effective_type = data.get("type", q.type)
    effective_options = data.get("options", q.options)
    if {"type", "options", "question", "answer", "left_items", "right_items"} & data.keys():
        _validate_answer_shape(
            effective_type,
            data.get("answer", q.answer),
            effective_options,
            data.get("question", q.question),
            left_items=data.get("left_items", q.left_items),
            right_items=data.get("right_items", q.right_items),
        )
    if "group_id" in data:
        _validate_group(db, data["group_id"], scope)
    bank = _get_allowed_bank(db, data.get("bank_id", q.bank_id), scope)
    effective_group = data.get("group_id", q.group_id)
    if bank and bank.group_id is not None and bank.group_id != effective_group:
        raise DomainError(BAD_REQUEST, "题目分组必须与题库分组一致")
    if data.get("tags"):
        _ensure_tags(db, data["tags"])
    # 白名单写入：不依赖 Pydantic schema 的字段列表兜底。
    # 否则日后给 QuestionUpdate 增加任何字段（例如 id/bank_id 之类）都会
    # 直接变成客户端可写，且改动发生在另一层、评审时不易察觉。
    for key, value in data.items():
        if key not in QUESTION_UPDATABLE_FIELDS:
            raise DomainError(BAD_REQUEST, f"不允许修改字段: {key}")
        setattr(q, key, value)
    db.commit()
    db.refresh(q)
    return q


def delete_question(db: Session, qid: int, scope: set[int] | None = None) -> None:
    """删除题目；被考试固化引用时拒绝（与 delete_bank 同一道防线）。

    `exam_questions.question_id` 是 ON DELETE CASCADE：若放任删除，已固化（含已发布/
    进行中）考试的卷面会被静默抽走该题，使 submit_exam 现算的 total_score/total_count
    随交卷时间变化、同一份考卷判分不一致，历史成绩也失去题目引用。
    因此与 delete_bank 一致：被引用则 409，提示改用其它处置方式。
    """
    q = db.get(Question, qid)
    if not q:
        raise DomainError(NOT_FOUND, "题目不存在")
    _validate_group(db, q.group_id, scope)
    used = db.execute(
        select(func.count()).select_from(ExamQuestion).where(ExamQuestion.question_id == qid)
    ).scalar_one()
    manual = _count_exams_referencing_questions(db, [qid])
    if used or manual:
        raise DomainError(
            CONFLICT,
            f"该题目被 {used} 场考试的卷面、{manual} 场考试的手工选题引用，无法删除；"
            "可将题目移出考试或改用关闭题库练习",
        )
    # 清理引用该题的练习记录与题目状态，避免留下孤儿数据（与 delete_bank 口径一致）
    stamps = [
        r[0] for r in db.execute(select(PracticeRecord.answered_at).where(PracticeRecord.question_id == qid)).all()
    ]
    db.execute(delete(PracticeRecord).where(PracticeRecord.question_id == qid))
    db.execute(delete(QuestionState).where(QuestionState.question_id == qid))
    db.delete(q)
    db.commit()
    _refresh_stats_after_delete(db, stamps)


def _refresh_stats_after_delete(db: Session, timestamps: list[str | None]) -> None:
    """删除源作答记录后重算受影响的每日聚合。

    `stats_user_daily` 是 `practice_records` 的物化聚合：删除源行而不重算，排行/面板
    会永久高于真实值。派生统计失败不应把已经提交的删除变成 500（否则管理员会误以为
    删除失败而重复操作），因此与 `exam/scoring.py` 交卷后的统计刷新同样降级为日志。
    """
    if not timestamps:
        return
    from app.services import stats_service

    try:
        stats_service.refresh_for_timestamps(db, timestamps)
    except Exception:  # noqa: BLE001  派生统计失败不阻断删除结果
        db.rollback()
        logger.warning("[question] 删除题目后统计重算失败，已忽略；下次刷新会兜底重算")


def type_stats(
    db: Session,
    bank_ids: list[int] | None = None,
    group_ids: list[int] | None = None,
    tags: list[str] | None = None,
    scope: set[int] | None = None,
) -> dict:
    """按组卷来源条件统计各题型可用题量（与 paper_service 的候选筛选口径一致）。

    用于题型配比编辑时提示“每个题型还剩多少题可选”，配额超过可用量时前端可即时预警。
    """
    conditions: list[ColumnElement[bool]] = []
    if scope is not None:
        conditions.append(Question.group_id.in_(scope))
    if bank_ids:
        conditions.append(Question.bank_id.in_(bank_ids))
    if group_ids:
        conditions.append(Question.group_id.in_(group_ids))

    counts = {t: 0 for t in QUESTION_TYPES}
    if tags:
        # 标签筛选下推到 SQL：用 SQLite JSON1 的 json_each 做**元素级**匹配
        # （`json_each.value IN (...)`，等价于「任一标签命中」），而不是子串比较。
        # 原实现把整表 (id,type,tags) 拉进 Python 再过滤：超管不传 bank/group 时
        # conditions 为空，一次请求即全表载入。
        tag_values = func.json_each(Question.tags).table_valued("value")
        conditions.append(exists(select(1).select_from(tag_values).where(tag_values.c.value.in_(tags))))

    # 聚合一律下推到 SQL（无标签时原实现已经是这样）
    agg = select(Question.type, func.count()).where(*conditions).group_by(Question.type)
    for qtype, total in db.execute(agg).all():
        if qtype in counts:
            counts[qtype] = int(total)
    return counts
