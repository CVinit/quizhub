"""排行榜：四维度 × 个人/分组 scope，含批量连续天数计算。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.group import Group
from app.models.stats import StatsUserDaily
from app.models.user import User
from app.services.stats.common import _date_str, _utcnow

# 「无分组」哨兵：stats_user_daily.group_id 为 NULL 时归入此组展示
_UNGROUPED_GID = 0
# 「不设下界」哨兵：range=all 时的最小日期。用 ISO 日期而非 "0000-00-00"
# （后者不是合法日期，只是恰好字典序最小，容易被误解析）
_MIN_DATE = "0000-01-01"
# streak 批量查询的分块大小：避免一次性拼出上千个绑定参数（SQLite 有参数上限）
_STREAK_CHUNK = 500


# ---------- 排行 ----------
# dimension: accuracy / count / score / streak
# scope: self(个人) / group(分组，聚合组内成员)
# range: 7d / 30d / all
def rank(
    db: Session,
    dimension: str,
    scope: str,
    range_: str,
    current_user_id: int | None = None,
    dept_scope: set[int] | None = None,
) -> list[dict]:
    """排行榜（各维度 Top10）。

    分组归属直接取 `stats_user_daily.group_id`（与聚合口径一致），不再另查
    `user_groups` 推断——两处口径不一致会让同一指标自相矛盾。
    展示名只对最终上榜的 ≤10 人批量查询，避免为全部用户构造无界 `IN (...)`。
    排序：`order_by(user_id)` 让并列名次的顺序确定，Python 端稳定排序后取 Top10。

    Args:
        db: 数据库会话。
        dimension: accuracy / count / score / streak。
        scope: self（个人榜）或 group（分组榜）。
        range_: 7d / 30d / all。
        current_user_id: 调用者 id，用于标记 `is_me`。
        dept_scope: 部门管理员的数据范围（分组 id 集合）；非 None 时只统计范围内用户，
            分组榜也只聚合范围内的分组。super_admin 传 None 表示全量。

    Returns:
        Top10 条目列表。
    """
    today = _utcnow()
    if range_ == "7d":
        since = _date_str(today - timedelta(days=6))
    elif range_ == "30d":
        since = _date_str(today - timedelta(days=29))
    else:
        since = _MIN_DATE

    stmt = (
        select(
            StatsUserDaily.user_id,
            func.sum(StatsUserDaily.answer_count).label("cnt"),
            func.sum(StatsUserDaily.correct_count).label("ok"),
            func.sum(StatsUserDaily.exam_score_sum).label("score"),
            func.sum(StatsUserDaily.exam_count).label("exam_cnt"),
            func.min(StatsUserDaily.group_id).label("group_id"),
        )
        .where(StatsUserDaily.date >= since)
        .group_by(StatsUserDaily.user_id)
        .order_by(StatsUserDaily.user_id)
    )
    if dept_scope is not None:
        # 部门管理员只能看到本部门子树内的排行：范围表达为 SQL 子查询，
        # 避免物化用户 id 后再拼 IN(...)（SQLite 绑定参数上限 / 大部门内存峰值）。
        from app.core.deps import user_ids_subquery

        stmt = stmt.where(StatsUserDaily.user_id.in_(user_ids_subquery(dept_scope)))
    rows = db.execute(stmt).all()

    user_ids = [r.user_id for r in rows]
    # streak 是唯一需要逐用户计算的维度；其余维度直接用聚合列。
    # 候选集先收敛到「今日或昨日有作答」的用户：连胜 > 0 的必要条件，且 range=all
    # 时若不收敛，一次请求会把全量历史 (user_id, date) 载入内存（单进程内存耗尽面）。
    # 非候选用户连胜恒为 0，由 `streak_map.get(uid, 0)` 兜底。
    streak_map = _streaks(db, _streak_candidates(db, user_ids), since) if dimension == "streak" else {}

    items = []
    for r in rows:
        uid = r.user_id
        cnt = int(r.cnt or 0)
        ok = int(r.ok or 0)
        score: float = float(r.score or 0)
        exam_cnt = int(r.exam_cnt or 0)
        value: float | int
        if dimension == "accuracy":
            value = round(ok / cnt * 100) if cnt else 0
        elif dimension == "count":
            value = cnt
        elif dimension == "score":
            value = round(score / exam_cnt, 1) if exam_cnt else 0
        elif dimension == "streak":
            value = streak_map.get(uid, 0)
        else:
            value = 0
        items.append(
            {
                "user_id": uid,
                "group_id": r.group_id,
                "value": value,
                "answer_count": cnt,
                "correct_count": ok,
                "exam_count": exam_cnt,
                "score_sum": score,
            }
        )

    # 分组维度：聚合到 group
    if scope == "group":
        return _rank_by_group(db, items, dimension, allowed_group_ids=dept_scope)

    items.sort(key=lambda x: x["value"], reverse=True)
    out = items[:10]
    # 只为上榜用户取展示名，避免为全部用户构造无界 IN (...)
    labels = _user_labels(db, [it["user_id"] for it in out])
    for it in out:
        it["name"] = labels.get(it["user_id"], f"#{it['user_id']}")
        for extra in ("group_id", "exam_count", "score_sum"):
            it.pop(extra, None)
    if current_user_id is not None:
        for it in out:
            if it["user_id"] == current_user_id:
                it["is_me"] = True
    return out


def _user_labels(db: Session, user_ids: list[int]) -> dict[int, str]:
    """批量取「用户 id → 展示名」。

    只查 name，绝不回退 email：排行榜对所有登录用户可见，回退邮箱即泄露他人 PII。
    姓名为空时由调用方回退为 `#<id>`。
    """
    if not user_ids:
        return {}
    return {
        uid: (name or f"#{uid}")
        for uid, name in db.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all()
    }


def _rank_by_group(
    db: Session,
    items: list[dict],
    dimension: str = "count",
    allowed_group_ids: set[int] | None = None,
) -> list[dict]:
    """把个人榜聚合为分组榜，返回 Top10。

    分组值由「分子/分母的合计」计算，而不是对成员的个人平均值再平均：
    考 1 次的成员与考 20 次的成员等权会让分组成绩严重失真。

    allowed_group_ids 非 None（部门管理员）时只聚合范围内的分组，
    未分组（哨兵 0）与范围外分组一律不进入分组榜。
    """
    groups: dict[int, dict[str, float]] = {}
    for it in items:
        gid = it.get("group_id") or _UNGROUPED_GID
        if allowed_group_ids is not None and gid not in allowed_group_ids:
            continue
        g = groups.setdefault(
            gid,
            {"answers": 0.0, "correct": 0.0, "exams": 0.0, "score": 0.0, "value_sum": 0.0, "members": 0.0},
        )
        g["answers"] += it["answer_count"]
        g["correct"] += it["correct_count"]
        g["exams"] += it["exam_count"]
        g["score"] += it["score_sum"]
        g["value_sum"] += it["value"]
        g["members"] += 1

    scored: list[tuple[int, float]] = []
    for gid, g in groups.items():
        value: float
        if dimension == "accuracy":
            value = round(g["correct"] / g["answers"] * 100, 1) if g["answers"] else 0.0
        elif dimension == "score":
            value = round(g["score"] / g["exams"], 1) if g["exams"] else 0.0
        elif dimension == "streak":
            value = round(g["value_sum"] / g["members"], 1) if g["members"] else 0.0
        else:  # count
            value = float(g["answers"])
        scored.append((gid, value))
    scored.sort(key=lambda x: (-x[1], x[0]))
    top = scored[:10]

    group_ids = [gid for gid, _ in top if gid]
    names = (
        {gid: name for gid, name in db.execute(select(Group.id, Group.name).where(Group.id.in_(group_ids))).all()}
        if group_ids
        else {}
    )
    # 未分组（gid=0）与已被删除的分组统一显示「未分组」
    return [{"name": names.get(gid, "未分组") if gid else "未分组", "value": value} for gid, value in top]


def _streak_candidates(db: Session, user_ids: list[int]) -> list[int]:
    """从候选用户中筛出「今日或昨日有作答」的子集。

    连胜 > 0 的用户必然在今日或昨日有作答（`_streak_from_dates` 的语义），
    因此该子集是完整且更小的输入集。`range=all` 时把输入从「有史以来所有用户」
    降到「近两日活跃用户」，避免单次请求把全量历史载入内存。

    Args:
        db: 数据库会话。
        user_ids: 排行聚合得到的候选用户 id 列表。

    Returns:
        今日或昨日 `answer_count > 0` 的用户 id（保持输入顺序）。
    """
    ids = list(user_ids)
    if not ids:
        return []
    today = _date_str(_utcnow())
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    active: set[int] = set()
    for start in range(0, len(ids), _STREAK_CHUNK):
        chunk = ids[start : start + _STREAK_CHUNK]
        rows = db.execute(
            select(StatsUserDaily.user_id)
            .where(
                StatsUserDaily.user_id.in_(chunk),
                StatsUserDaily.answer_count > 0,
                StatsUserDaily.date.in_((today, yesterday)),
            )
            .distinct()
        ).all()
        active.update(r[0] for r in rows)
    return [uid for uid in ids if uid in active]


def _streaks(db: Session, user_ids: Iterable[int], since: str) -> dict[int, int]:
    """批量计算连续答题天数。

    since 用于限定回看范围：连胜不可能早于查询窗口起点，
    因此只取窗口内记录，避免全量扫描用户历史（大表上是 O(用户数 × 历史长度)）。
    原实现每个用户一次查询，这里按 `_STREAK_CHUNK` 分块取回窗口内所有
    (user_id, date) 再在内存里计算——分块是为了不把上千个用户 id 拼进一条 IN(...)。
    """
    ids = list(user_ids)
    if not ids:
        return {}
    dates_by_user: dict[int, list[str]] = defaultdict(list)
    for start in range(0, len(ids), _STREAK_CHUNK):
        chunk = ids[start : start + _STREAK_CHUNK]
        rows = db.execute(
            select(StatsUserDaily.user_id, StatsUserDaily.date)
            .where(
                StatsUserDaily.user_id.in_(chunk),
                StatsUserDaily.answer_count > 0,
                StatsUserDaily.date >= since,
            )
            .order_by(StatsUserDaily.user_id, StatsUserDaily.date.desc())
        ).all()
        for uid, date in rows:
            dates_by_user[uid].append(date)
    return {uid: _streak_from_dates(dates) for uid, dates in dates_by_user.items()}


def _streak_from_dates(dates: list[str]) -> int:
    """从「按日期降序」的列表计算连续天数。

    今日未答但昨日已答，也算昨日起的连胜（原有语义）。
    """
    if not dates:
        return 0
    today = _date_str(_utcnow())
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    # 连胜起点允许是今日或昨日（今日还没答题时从昨日继续数）
    expect = today
    if dates[0] == yesterday:
        expect = yesterday
    elif dates[0] != today:
        # 既不是今日也不是昨日 → 今日与昨日均无记录，连胜中断
        return 0
    streak = 0
    for d in dates:
        if d == expect:
            streak += 1
            expect = (datetime.strptime(expect, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        elif d < expect:
            break
    return streak
