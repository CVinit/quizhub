"""规则组卷：按题型/难度配比、来源筛选、随机种子抽题。

config 结构：
{
  "type_quota": {"单选题": 10, "多选题": 5, "判断题": 5, "填空题": 3, "简答题": 2},
  "difficulty_dist": {"1": 0.3, "2": 0.5, "3": 0.2},  # 可选，按比例
  "bank_ids": [1, 2],         # 来源题库筛选（Question.bank_id）—— 选中哪几个题库
  "group_ids": [1, 2],       # 来源分组筛选（题库 group_id）
  "tags": ["网络"],          # 来源标签筛选（任一命中）
  "allow_duplicate": false,
  "seed": 12345,
  "max_questions": 100,
  "order_mode": "bank"        # 出题顺序：bank(默认,与导入顺序一致) / random / grouped
}

返回固化题目清单 id 列表 + 每题分值。
"""

from __future__ import annotations

import random

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models.question import DEFAULT_QUESTION_SCORE, Question
from app.services.question_service import QUESTION_TYPES

# 出题顺序模式：
# - bank    抽题后按题目入库顺序（Question.id 升序）排列，与题库导入顺序一致（默认）
# - random  完全随机（抽中顺序即出题顺序）
# - grouped 按题型分组（同题型连在一起），组内随机
ORDER_MODES = ("bank", "random", "grouped")
DEFAULT_ORDER_MODE = "bank"

# 题型分配策略：
# - proportional   按范围内各题型可用题量比例分配（默认）
# - even           各题型等量分配
ALLOCATION_MODES = ("proportional", "even")
DEFAULT_ALLOCATION = "proportional"

# 组卷候选集上限：单场最多 max_q(≤1000) 道题，但分层抽样要在各 (题型,难度) 池内
# 独立抽取，故允许较大的候选池。超过此上限直接 400 提示收窄范围，而不是把整张
# 题库表载入内存（原实现无上限）。
_CANDIDATE_MAX = 20000


def largest_remainder(weights: dict[str, float], total: int) -> dict[str, int]:
    """最大余数法：把 total 个名额按 weights 比例分配为整数，合计恰为 total。

    先按 floor(total * w / Σw) 分配，再把剩余的 total - Σfloor 个名额按小数部分
    从大到小依次 +1（小数部分相同时按题型在 QUESTION_TYPES 中的顺序稳定排序，
    保证同输入必得同输出，便于测试与复现）。

    weights 全为 0 或为空时返回全 0（由调用方兜底）。
    """
    keys = [t for t in QUESTION_TYPES if t in weights]
    # 保留非标准键（理论上不会出现，QUESTION_TYPES 为白名单），确保不丢键
    keys += [k for k in weights if k not in keys]
    base = {k: 0 for k in keys}
    weight_sum = sum(max(0.0, float(weights.get(k, 0))) for k in keys)
    if total <= 0 or weight_sum <= 0:
        return base

    remainders: list[tuple[float, int, str]] = []
    allocated = 0
    for idx, k in enumerate(keys):
        exact = total * max(0.0, float(weights.get(k, 0))) / weight_sum
        floor_v = int(exact)
        base[k] = floor_v
        allocated += floor_v
        remainders.append((exact - floor_v, -idx, k))  # -idx 使同余数时按题型顺序靠前者优先

    # 余数从大到小补足；-idx 参与排序保证稳定性
    remainders.sort(reverse=True)
    for i in range(total - allocated):
        base[remainders[i % len(remainders)][2]] += 1
    return base


def allocate_quota(
    avail: dict[str, int],
    size: int,
    manual_quota: dict[str, int] | None = None,
    allocation: str = DEFAULT_ALLOCATION,
) -> dict[str, int]:
    """按题型可用题量与用户设置，计算最终题型配额（合计严格等于 size）。

    Args:
        avail: 范围内各题型可用题量，如 {"单选题": 30, "多选题": 10}。
        size: 用户设定的题量（已由调用方按可用总量下调过）。
        manual_quota: 用户手填的题型配额；None/空表示自动分配。
        allocation: 自动分配策略（proportional / even）。

    Returns:
        题型 → 配额，Σ 恰好等于 size。

    Raises:
        DomainError: 入参非法（未知题型、负数、自动分配下可用题量为 0、
            手填合计与 size 不一致）。

    注：本函数不负责"逐题型钳制超出可用量"——手填值超出 avail 时直接报错，
    而不是静默改写用户输入，避免系统悄悄改掉用户填的数字。
    """
    if allocation not in ALLOCATION_MODES:
        raise DomainError(status.HTTP_400_BAD_REQUEST, f"题型分配策略必须是 {ALLOCATION_MODES} 之一")
    avail = avail or {}
    unknown = [t for t in avail if t not in QUESTION_TYPES]
    if unknown:
        raise DomainError(status.HTTP_400_BAD_REQUEST, f"未知题型：{'、'.join(unknown)}")

    if manual_quota:
        unknown_manual = [t for t in manual_quota if t not in QUESTION_TYPES]
        if unknown_manual:
            raise DomainError(status.HTTP_400_BAD_REQUEST, f"未知题型：{'、'.join(unknown_manual)}")
        bad = [t for t, n in manual_quota.items() if not isinstance(n, int) or isinstance(n, bool) or n < 0]
        if bad:
            raise DomainError(status.HTTP_400_BAD_REQUEST, "题型数量必须是非负整数")
        quota = {t: int(n) for t, n in manual_quota.items() if int(n) > 0}
        over = [f"{t}（可用 {avail.get(t, 0)} 题）" for t, n in quota.items() if n > avail.get(t, 0)]
        if over:
            raise DomainError(status.HTTP_400_BAD_REQUEST, f"题型数量超出可用题量：{'、'.join(over)}")
        quota_sum = sum(quota.values())
        if quota_sum != size:
            raise DomainError(
                status.HTTP_400_BAD_REQUEST,
                f"各题型数量合计 {quota_sum} 题，与设定题量 {size} 题不一致",
            )
        return quota

    # 自动分配：按可用题量加权（等量策略下各题型权重相同）
    usable = {t: max(0, int(avail.get(t, 0))) for t in QUESTION_TYPES if int(avail.get(t, 0)) > 0}
    if not usable:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "所选范围内没有可用题目")
    weights = {t: 1.0 for t in usable} if allocation == "even" else {t: float(n) for t, n in usable.items()}
    quota = largest_remainder(weights, size)
    # largest_remainder 可能给某种题型分配超过其可用量的数量（权重小但余数补偿），
    # 需钳制后把溢出份额回流给尚有余量的题型。
    return _clamp_and_redistribute(quota, usable, size)


def _clamp_and_redistribute(quota: dict[str, int], avail: dict[str, int], size: int) -> dict[str, int]:
    """把配额逐题型钳制到可用量，并把溢出的份额回流给尚有余量的题型。

    最多迭代 len(avail) 轮以保证收敛；若所有题型都已到可用上限（即
    Σavail < size），返回实际可出题量（合计 < size），由调用方决定是否下调 size。
    """
    quota = {t: min(n, avail.get(t, 0)) for t, n in quota.items()}
    for _ in range(len(avail) + 1):
        deficit = size - sum(quota.values())
        if deficit <= 0:
            break
        room = {t: avail[t] - quota.get(t, 0) for t in avail if avail[t] - quota.get(t, 0) > 0}
        if not room:
            break  # 范围内题量不足，无法补满
        weights = {t: float(r) for t, r in room.items()}
        add = largest_remainder(weights, deficit)
        for t, n in add.items():
            quota[t] = quota.get(t, 0) + min(n, room[t])
    return {t: n for t, n in quota.items() if n > 0}


def generate_paper(db: Session, config: dict, scope: set[int] | None = None) -> dict:
    """按规则生成试卷，返回 {question_ids:[...], scores:{qid:score}, total_score}。

    Args:
        db: 数据库会话。
        config: 组卷规则（题型配额、难度配比、来源筛选、顺序模式、随机种子等）。
        scope: 调用者可访问的分组 id 集合；None 表示不限制（超级管理员）。
            非 None 时作为硬上限与请求内的 group_ids 取交集，
            防止部门管理员通过伪造 group_ids 抽到其他部门的题目。

    Returns:
        含 question_ids / scores / total_score 的字典。
    """
    type_quota: dict[str, int] = config.get("type_quota") or {}
    difficulty_dist: dict[str, float] = config.get("difficulty_dist") or {}
    seed = config.get("seed")
    allow_dup = config.get("allow_duplicate", False)
    max_q = config.get("max_questions", 100)
    if not isinstance(max_q, int) or isinstance(max_q, bool) or not 1 <= max_q <= 1000:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "max_questions 必须在 1~1000 之间")
    if allow_dup:
        raise DomainError(status.HTTP_400_BAD_REQUEST, "当前试卷模型不支持重复题目")
    if not isinstance(type_quota, dict) or any(
        not isinstance(quota, int) or isinstance(quota, bool) or quota < 0 or quota > 1000
        for quota in type_quota.values()
    ):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "题型数量配置无效")
    if not isinstance(difficulty_dist, dict):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "难度配比配置无效")
    try:
        ratios = [float(ratio) for ratio in difficulty_dist.values()]
        difficulty_keys = {int(key) for key in difficulty_dist}
    except (TypeError, ValueError):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "难度配比配置无效") from None
    if (
        any(ratio < 0 or ratio > 1 for ratio in ratios)
        or sum(ratios) > 1.000001
        or not difficulty_keys.issubset({1, 2, 3})
    ):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "难度配比必须是 1~3 的非负比例，合计不能超过 1")
    bank_ids = config.get("bank_ids") or []
    group_ids = config.get("group_ids") or []
    tags = config.get("tags") or []
    order_mode = config.get("order_mode") or DEFAULT_ORDER_MODE
    if order_mode not in ORDER_MODES:
        raise DomainError(status.HTTP_400_BAD_REQUEST, f"出题顺序必须是 {ORDER_MODES} 之一")

    rng = random.Random(seed)

    # 候选题库（按来源筛选，只投影必要列，避免拉取 options/answer/analysis 等 JSON 大列）
    stmt = select(Question.id, Question.type, Question.difficulty, Question.score, Question.tags)
    # 数据范围硬上限：先于请求内的筛选生效，且不可被请求参数放宽
    if scope is not None:
        stmt = stmt.where(Question.group_id.in_(scope))
    if bank_ids:
        stmt = stmt.where(Question.bank_id.in_(bank_ids))
    if group_ids:
        stmt = stmt.where(Question.group_id.in_(group_ids))
    candidates = list(db.execute(stmt.limit(_CANDIDATE_MAX + 1)).all())
    # 分层抽样需要在各 (题型, 难度) 池内独立抽取，无法只取前 max_q 条；
    # 但也绝不能把整张题库表无界载入内存。超过上限时给出明确 400（而非静默截断，
    # 静默截断会让「抽到的题」与实际池子不一致且难以察觉）。
    if len(candidates) > _CANDIDATE_MAX:
        raise DomainError(
            status.HTTP_400_BAD_REQUEST,
            f"可用题目超过 {_CANDIDATE_MAX} 道，请缩小题库/分组/标签范围后重试",
        )
    # 标签匹配只在 Python 端做：SQLite 的 JSON 列没有真正的集合包含语义，
    # `.contains(tags)` 会退化成对序列化文本的子串比较，静默丢掉多标签题目
    # （["网络","安全"] 匹配不到 ["网络"]），而 SQL 预过滤一旦漏掉就无法挽回。
    if tags:
        candidates = [c for c in candidates if c[4] and any(t in c[4] for t in tags)]

    # 按 (题型, 难度) 分层，便于难度配比
    by_type_diff: dict[tuple[str, int], list[tuple]] = {}
    by_type: dict[str, list[tuple]] = {}
    type_of: dict[int, str] = {}  # qid -> 题型，供 grouped 排序使用
    for cid, ctype, cdiff, cscore, _ctags in candidates:
        # `cscore or DEFAULT` 会把合法的 0 分题（score 允许 0）当成缺失值补成 2 分，
        # 使总分为 0 的题目凭空加分并可能翻转及格结果；只有 None 才回落默认值。
        row = (cid, ctype, cdiff, cscore if cscore is not None else DEFAULT_QUESTION_SCORE)
        by_type.setdefault(ctype, []).append(row)
        by_type_diff.setdefault((ctype, cdiff), []).append(row)
        type_of[cid] = ctype

    chosen_ids: list[int] = []
    scores: dict[int, float] = {}
    total_score = 0.0

    for qtype, quota in type_quota.items():
        # 若指定了难度配比，按比例在各难度间分配 quota；否则从该题型全池抽样
        if difficulty_dist:
            picked: list[tuple] = []
            # 计算各难度应抽数量（向下取整，余数补给首个有富余的难度）
            diff_quotas: dict[int, int] = {}
            remaining = quota
            for diff_str, ratio in difficulty_dist.items():
                n = int(quota * float(ratio))
                diff_quotas[int(diff_str)] = n
                remaining -= n
            if remaining > 0 and difficulty_dist:
                # 余数补给配比最大或首个难度
                first_diff = int(next(iter(difficulty_dist)))
                diff_quotas[first_diff] = diff_quotas.get(first_diff, 0) + remaining
            for diff, n in diff_quotas.items():
                pool = list(by_type_diff.get((qtype, diff), []))
                rng.shuffle(pool)
                picked.extend(pool[:n])
            # 不足 quota 时从该题型全池补抽
            if len(picked) < quota:
                full_pool = list(by_type.get(qtype, []))
                existing = {p[0] for p in picked}
                extra = [p for p in full_pool if p[0] not in existing]
                rng.shuffle(extra)
                picked.extend(extra[: quota - len(picked)])
            pool = picked
        else:
            pool = list(by_type.get(qtype, []))
            rng.shuffle(pool)

        pick_n = min(quota, len(pool))
        if pick_n == 0:
            continue
        picked_rows = pool[:pick_n]
        for row in picked_rows:
            qid = row[0]
            qscore = row[3]
            if qid not in chosen_ids:
                chosen_ids.append(qid)
                scores[qid] = qscore
                total_score += qscore

    # 上限
    if len(chosen_ids) > max_q:
        chosen_ids = chosen_ids[:max_q]
        # scores 必须同步裁剪：否则返回的 scores 含未入选题目，违反
        # 「question_ids + 每题分值」的返回契约（后续若按 scores 固化会多出幽灵题目）。
        scores = {qid: scores[qid] for qid in chosen_ids}
        total_score = sum(scores.values())

    # 出题顺序：抽题阶段按题型分层（保证配额/难度配比准确），此处仅对最终清单重新排序。
    # bank 模式按题目入库顺序（Question.id 升序）排列 —— 与题库导入（Excel 行序）一致。
    if order_mode == "bank":
        chosen_ids = sorted(chosen_ids)
    elif order_mode == "grouped":
        # 按标准题型顺序分组（单选→多选→判断→填空→简答→拖拽），组内保持入库顺序
        type_rank = {t: i for i, t in enumerate(QUESTION_TYPES)}
        chosen_ids = sorted(chosen_ids, key=lambda qid: (type_rank.get(type_of.get(qid, ""), 99), qid))
    else:  # random
        # 抽题阶段按题型分层累积，此处必须整体打散，否则观感与 grouped 无异
        rng.shuffle(chosen_ids)

    return {
        "question_ids": chosen_ids,
        "scores": scores,
        "total_score": total_score,
        "count": len(chosen_ids),
        "order_mode": order_mode,
    }
