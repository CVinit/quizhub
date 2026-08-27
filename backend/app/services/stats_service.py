"""统计预聚合与面板/排行查询。

策略：每日把 practice_records / exam_results 聚合写入 stats_user_daily
（按 用户×日期×主分组 唯一），排行与面板命中预聚合表。
- 启动时刷新「今日 + 昨日」；提供手动刷新接口。
- 规模 ≤100 用户，预聚合表足够支撑 Top10 与四维度查询。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.group import Group, UserGroup
from app.models.question import Question
from app.models.record import ExamResult, PracticeRecord, QuestionState
from app.models.stats import StatsUserDaily
from app.models.user import User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _date_str(dt: datetime) -> str:
    """按本地日期 YYYY-MM-DD 取（UTC 偏移 +8 近似）。"""
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d")


def refresh_daily(db: Session, date_str: str) -> int:
    """重算某日 stats_user_daily，返回写入条数。

    时区处理：PracticeRecord.answered_at / ExamResult.created_at 以 UTC ISO 存储，
    此处按本地(CST +8)日期归属——用 SQL date() 把 UTC 时间偏移 +8 小时后取日期，
    避免原实现 LIKE '{date_str}%' 把 UTC 与本地日期前缀错配。
    """
    from sqlalchemy import text

    # 该日练习记录（UTC + 8h 后取日期 = 本地日期）
    practice_rows = db.execute(
        text(
            "SELECT user_id, COUNT(*) AS cnt, SUM(is_correct) AS ok "
            "FROM practice_records "
            "WHERE date(datetime(answered_at, '+8 hours')) = :d "
            "GROUP BY user_id"
        ),
        {"d": date_str},
    ).all()

    # 该日已交卷成绩（按 created_at 本地日期归属）
    exam_rows = db.execute(
        text(
            "SELECT user_id, COUNT(*) AS cnt, SUM(score) AS score, SUM(passed) AS pass_cnt "
            "FROM exam_results "
            "WHERE date(datetime(created_at, '+8 hours')) = :d "
            "GROUP BY user_id"
        ),
        {"d": date_str},
    ).all()

    practice_map = {r.user_id: r for r in practice_rows}
    exam_map = {r.user_id: r for r in exam_rows}
    user_ids = set(practice_map) | set(exam_map)
    if not user_ids:
        return 0

    # 清空当日旧数据
    db.execute(StatsUserDaily.__table__.delete().where(StatsUserDaily.date == date_str))

    # 一次查询所有用户的分组，消除 N+1
    gid_map: dict[int, int] = {
        r[0]: r[1]
        for r in db.execute(
            select(UserGroup.user_id, func.min(UserGroup.group_id))
            .where(UserGroup.user_id.in_(list(user_ids)))
            .group_by(UserGroup.user_id)
        ).all()
    }

    written = 0
    for uid in user_ids:
        pr = practice_map.get(uid)
        er = exam_map.get(uid)
        group_id = gid_map.get(uid)
        db.add(
            StatsUserDaily(
                user_id=uid,
                date=date_str,
                group_id=group_id,
                answer_count=pr.cnt if pr else 0,
                correct_count=int(pr.ok or 0) if pr else 0,
                wrong_count=(pr.cnt - int(pr.ok or 0)) if pr else 0,
                exam_count=er.cnt if er else 0,
                exam_score_sum=int(er.score or 0) if er else 0,
                exam_pass_count=int(er.pass_cnt or 0) if er else 0,
            )
        )
        written += 1
    db.commit()
    return written


def refresh_recent(db: Session, days: int = 30) -> int:
    """刷新最近 N 天。"""
    total = 0
    today = _utcnow()
    for i in range(days):
        total += refresh_daily(db, _date_str(today - timedelta(days=i)))
    return total


def startup_refresh(db: Session) -> None:
    """启动触发：刷新今日与昨日。"""
    today = _utcnow()
    refresh_daily(db, _date_str(today))
    refresh_daily(db, _date_str(today - timedelta(days=1)))


# ---------- 用户面板 ----------
def user_panel(db: Session, user: User) -> dict:
    total_q = db.execute(select(func.count(Question.id))).scalar() or 0
    # 用 SQL 聚合替代全量载入 QuestionState 后 Python 计数
    row = db.execute(
        select(
            func.sum(func.iif(QuestionState.status != "unanswered", 1, 0)).label("practiced"),
            func.sum(func.iif(QuestionState.status.in_(("correct", "mastered")), 1, 0)).label("correct"),
            func.sum(func.iif(QuestionState.status == "wrong", 1, 0)).label("wrong"),
            func.sum(func.iif(QuestionState.marked == True, 1, 0)).label("marked"),  # noqa: E712
        ).where(QuestionState.user_id == user.id)
    ).one()
    practiced = int(row.practiced or 0)
    correct = int(row.correct or 0)
    wrong = int(row.wrong or 0)
    marked = int(row.marked or 0)
    accuracy = round(correct / practiced * 100) if practiced else 0

    # 最近 5 次考试
    results = (
        db.execute(select(ExamResult).where(ExamResult.user_id == user.id).order_by(ExamResult.id.desc()).limit(5))
        .scalars()
        .all()
    )
    recent_exams = []
    for r in results:
        from app.models.exam import ExamDefinition

        e = db.get(ExamDefinition, r.exam_definition_id)
        recent_exams.append(
            {
                "name": e.name if e else "",
                "score": r.score,
                "total_score": r.total_score,
                "passed": r.passed,
                "published": r.published,
            }
        )
    return {
        "total": total_q,
        "practiced": practiced,
        "correct": correct,
        "wrong": wrong,
        "marked": marked,
        "accuracy": accuracy,
        "recent_exams": recent_exams,
    }


# ---------- 管理端概览 ----------
def admin_overview(db: Session) -> dict:
    from app.models.exam import ExamDefinition
    from app.models.record import ShortAnswerReview

    total_users = db.execute(select(func.count(User.id))).scalar() or 0
    active_users = db.execute(select(func.count(User.id)).where(User.status == "active")).scalar() or 0
    pending_approvals = db.execute(select(func.count(User.id)).where(User.status == "pending")).scalar() or 0
    total_questions = db.execute(select(func.count(Question.id))).scalar() or 0

    # 完成率：有练习记录的题目数 / 总题数
    practiced_q = (
        db.execute(
            select(func.count(func.distinct(QuestionState.question_id))).where(QuestionState.status != "unanswered")
        ).scalar()
        or 0
    )
    completion_rate = round(practiced_q / total_questions * 100) if total_questions else 0

    # 全站正确率
    total_answers = db.execute(select(func.count(PracticeRecord.id))).scalar() or 0
    correct_answers = (
        db.execute(
            select(func.count(PracticeRecord.id)).where(PracticeRecord.is_correct == True)  # noqa: E712
        ).scalar()
        or 0
    )
    accuracy = round(correct_answers / total_answers * 100) if total_answers else 0

    total_exams = db.execute(select(func.count(ExamDefinition.id)).where(ExamDefinition.type == "formal")).scalar() or 0
    pending_reviews = (
        db.execute(select(func.count(ShortAnswerReview.id)).where(ShortAnswerReview.verdict.is_(None))).scalar() or 0
    )

    # 今日活跃用户（今日有练习记录）
    today = _date_str(_utcnow())
    today_active = (
        db.execute(
            select(func.count(func.distinct(StatsUserDaily.user_id))).where(StatsUserDaily.date == today)
        ).scalar()
        or 0
    )

    return {
        "total_users": total_users,
        "active_users": active_users,
        "pending_approvals": pending_approvals,
        "total_questions": total_questions,
        "completion_rate": completion_rate,
        "accuracy": accuracy,
        "total_exams": total_exams,
        "pending_reviews": pending_reviews,
        "today_active": today_active,
    }


# ---------- 排行 ----------
# dimension: accuracy / count / score / streak
# scope: self(个人) / group(分组，聚合组内成员)
# range: 7d / 30d / all
def rank(db: Session, dimension: str, scope: str, range_: str, current_user_id: int | None = None) -> list[dict]:
    today = _utcnow()
    if range_ == "7d":
        since = _date_str(today - timedelta(days=6))
    elif range_ == "30d":
        since = _date_str(today - timedelta(days=29))
    else:
        since = "0000-00-00"

    stmt = (
        select(
            StatsUserDaily.user_id,
            func.sum(StatsUserDaily.answer_count).label("cnt"),
            func.sum(StatsUserDaily.correct_count).label("ok"),
            func.sum(StatsUserDaily.exam_score_sum).label("score"),
            func.sum(StatsUserDaily.exam_count).label("exam_cnt"),
        )
        .where(StatsUserDaily.date >= since)
        .group_by(StatsUserDaily.user_id)
    )

    rows = db.execute(stmt).all()

    # 计算各维度值
    items = []
    for r in rows:
        uid = r.user_id
        cnt = int(r.cnt or 0)
        ok = int(r.ok or 0)
        score = int(r.score or 0)
        exam_cnt = int(r.exam_cnt or 0)
        if dimension == "accuracy":
            value = round(ok / cnt * 100) if cnt else 0
        elif dimension == "count":
            value = cnt
        elif dimension == "score":
            value = round(score / exam_cnt, 1) if exam_cnt else 0
        elif dimension == "streak":
            value = _streak(db, uid, since)
        else:
            value = 0
        u = db.get(User, uid)
        items.append(
            {
                "user_id": uid,
                "name": u.name or u.email if u else f"#{uid}",
                "value": value,
                "answer_count": cnt,
                "correct_count": ok,
            }
        )

    # 分组维度：聚合到 group
    if scope == "group":
        group_items = {}
        for it in items:
            gid_row = db.execute(select(UserGroup.group_id).where(UserGroup.user_id == it["user_id"]).limit(1)).first()
            gid = gid_row[0] if gid_row else 0
            g = group_items.setdefault(gid, {"group_id": gid, "value": 0, "count": 0, "members": 0})
            g["value"] += it["value"]
            g["count"] += 1
            g["members"] += 1
        out = []
        for gid, g in group_items.items():
            gname = "未分组"
            if gid:
                grp = db.get(Group, gid)
                if grp:
                    gname = grp.name
            out.append(
                {
                    "name": gname,
                    "value": round(g["value"] / g["count"], 1) if g["count"] else 0,
                }
            )
        out.sort(key=lambda x: x["value"], reverse=True)
        return out[:10]

    items.sort(key=lambda x: x["value"], reverse=True)
    out = items[:10]
    if current_user_id:
        for it in out:
            if it["user_id"] == current_user_id:
                it["is_me"] = True
    return out


def _streak(db: Session, user_id: int, since: str) -> int:
    """连续答题天数（从今日往前数）。今日未答但昨日已答，也算昨日起的连胜。"""
    dates = [
        r[0]
        for r in db.execute(
            select(StatsUserDaily.date)
            .where(StatsUserDaily.user_id == user_id, StatsUserDaily.answer_count > 0)
            .order_by(StatsUserDaily.date.desc())
        ).all()
    ]
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
