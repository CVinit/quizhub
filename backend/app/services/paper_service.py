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
  "max_questions": 100
}

返回固化题目清单 id 列表 + 每题分值。
"""

from __future__ import annotations

import random

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.question import Question


def generate_paper(db: Session, config: dict) -> dict:
    """按规则生成试卷，返回 {question_ids:[...], scores:{qid:score}, total_score}."""
    type_quota: dict[str, int] = config.get("type_quota") or {}
    difficulty_dist: dict[str, float] = config.get("difficulty_dist") or {}
    seed = config.get("seed")
    allow_dup = config.get("allow_duplicate", False)
    max_q = config.get("max_questions", 100)
    if not isinstance(max_q, int) or isinstance(max_q, bool) or not 1 <= max_q <= 1000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "max_questions 必须在 1~1000 之间")
    if allow_dup:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "当前试卷模型不支持重复题目")
    if not isinstance(type_quota, dict) or any(
        not isinstance(quota, int) or isinstance(quota, bool) or quota < 0 or quota > 1000
        for quota in type_quota.values()
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "题型数量配置无效")
    if not isinstance(difficulty_dist, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "难度配比配置无效")
    try:
        ratios = [float(ratio) for ratio in difficulty_dist.values()]
        difficulty_keys = {int(key) for key in difficulty_dist}
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "难度配比配置无效") from None
    if (
        any(ratio < 0 or ratio > 1 for ratio in ratios)
        or sum(ratios) > 1.000001
        or not difficulty_keys.issubset({1, 2, 3})
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "难度配比必须是 1~3 的非负比例，合计不能超过 1")
    bank_ids = config.get("bank_ids") or []
    group_ids = config.get("group_ids") or []
    tags = config.get("tags") or []

    rng = random.Random(seed)

    # 候选题库（按来源筛选，只投影必要列，避免拉取 options/answer/analysis 等 JSON 大列）
    stmt = select(Question.id, Question.type, Question.difficulty, Question.score)
    if bank_ids:
        stmt = stmt.where(Question.bank_id.in_(bank_ids))
    if group_ids:
        stmt = stmt.where(Question.group_id.in_(group_ids))
    if tags:
        stmt = stmt.where(Question.tags.contains(tags))  # JSON 包含，近似
    candidates = list(db.execute(stmt).all())
    # tags 用 Python 端精确过滤兜底（SQLite JSON 查询能力有限）
    if tags:
        tag_rows = list(
            db.execute(select(Question.id, Question.tags).where(Question.id.in_([c[0] for c in candidates]))).all()
        )
        tag_map = {r[0]: r[1] for r in tag_rows}
        candidates = [c for c in candidates if tag_map.get(c[0]) and any(t in tag_map[c[0]] for t in tags)]

    # 按 (题型, 难度) 分层，便于难度配比
    by_type_diff: dict[tuple[str, int], list[tuple]] = {}
    by_type: dict[str, list[tuple]] = {}
    for cid, ctype, cdiff, cscore in candidates:
        row = (cid, ctype, cdiff, cscore or 2)
        by_type.setdefault(ctype, []).append(row)
        by_type_diff.setdefault((ctype, cdiff), []).append(row)

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
        if pick_n == 0 and not allow_dup:
            continue
        picked_rows = pool[:pick_n]
        # 允许重复且不足时补抽
        if allow_dup and pick_n < quota and pool:
            while len(picked_rows) < quota and pool:
                picked_rows.append(rng.choice(pool))

        for row in picked_rows[:quota] if allow_dup else picked_rows:
            qid = row[0]
            qscore = row[3]
            if qid not in chosen_ids or allow_dup:
                chosen_ids.append(qid)
                scores[qid] = qscore
                total_score += qscore

    # 上限
    if len(chosen_ids) > max_q:
        chosen_ids = chosen_ids[:max_q]
        total_score = sum(scores[q] for q in chosen_ids)

    return {
        "question_ids": chosen_ids,
        "scores": scores,
        "total_score": total_score,
        "count": len(chosen_ids),
    }
