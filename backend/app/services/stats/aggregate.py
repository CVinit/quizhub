"""每日统计预聚合：按业务日把练习/考试源数据聚合进 stats_user_daily。"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.exam import ExamDefinition
from app.models.group import UserGroup
from app.models.record import ExamResult, PracticeRecord
from app.models.stats import StatsUserDaily
from app.models.user import User
from app.services.stats.common import _date_str, _day_bounds_utc, _utcnow

logger = logging.getLogger("quizhub")

# 考试统计口径（唯一来源，refresh_daily 与 refresh_user_daily 共用）：
# 只有「已公布」的**正式**考试成绩进入日均聚合（管理端概览按它统计）。
# - 未公布（含简答待复核）成绩只是客观题小计，提前计入等于把未定成绩算成已得；
# - 模拟考是练习性质，明确不计入（见
#   docs/superpowers/specs/2026-09-17-mock-exam-redesign.md:192）。
_COUNTABLE_EXAM_TYPE = "formal"


def _countable_exam_conditions() -> tuple:
    """返回 JOIN ExamDefinition 后必须满足的统计口径条件。"""
    return (ExamDefinition.type == _COUNTABLE_EXAM_TYPE, ExamResult.published.is_(True))


def _acquire_write_lock(db: Session) -> None:
    """在读快照前取得 SQLite 写锁（BEGIN IMMEDIATE）。

    「读快照 → 全量 DELETE → 重建」这类重算必须整体处于同一个写事务内：pysqlite 只在
    DML 前 BEGIN，SELECT 走自动提交，若并发写入在快照之后、DELETE 之前提交，新行会被
    DELETE 抹掉并用旧快照重建（丢失更新）。

    仅在会话处于干净状态（调用方已提交，或本函数上一次调用已提交）时取锁；若已在事务中
    （例如被上层包在更大的事务里，practice_service 的「同事务提交」契约），保持原行为
    不额外加锁。

    Args:
        db: 数据库会话。
    """
    if not db.in_transaction():
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def refresh_daily(db: Session, date_str: str) -> int:
    """重算某日 stats_user_daily，返回写入条数。

    时区处理：PracticeRecord.answered_at / ExamResult.created_at 均以 UTC ISO 字符串存储；
    归属日期按业务时区（config.BUSINESS_TZ）计算，先换算成 UTC 区间再比较，
    避免时区错配与 SQLite datetime() 的解析陷阱。

    并发：本函数是「读快照 → 全量 DELETE → 重建」，必须整体处于同一个写事务内。
    pysqlite 默认只在 DML 前 BEGIN，SELECT 走自动提交；若并发调用方（每次作答/自评/复核后的
    `refresh_user_daily`）在本函数读快照之后、DELETE 之前提交了当日新行，这些新行会被
    全量 DELETE 抹掉并用旧快照重建（丢失更新）。因此先取写锁（BEGIN IMMEDIATE）。
    """
    _acquire_write_lock(db)
    day_start, day_end = _day_bounds_utc(date_str)

    practice_rows = db.execute(
        select(
            PracticeRecord.user_id,
            # is_correct 为三态：True/False/NULL（简答自评前）。NULL 既不是答对也不是答错，
            # 直接 `count(*)` 再 `cnt - sum(ok)` 会把未自评简答全部算成错题。
            # 这里按状态分别计数，保证 answer_count == correct_count + wrong_count。
            func.sum(func.iif(PracticeRecord.is_correct.is_not(None), 1, 0)).label("cnt"),
            func.sum(func.iif(PracticeRecord.is_correct.is_(True), 1, 0)).label("ok"),
            func.sum(func.iif(PracticeRecord.is_correct.is_(False), 1, 0)).label("bad"),
        )
        .where(PracticeRecord.answered_at >= day_start, PracticeRecord.answered_at < day_end)
        .group_by(PracticeRecord.user_id)
    ).all()

    exam_rows = db.execute(
        select(
            ExamResult.user_id,
            func.count().label("cnt"),
            func.sum(ExamResult.score).label("score"),
            func.sum(ExamResult.passed).label("pass_cnt"),
        )
        .join(ExamDefinition, ExamDefinition.id == ExamResult.exam_definition_id)
        .where(
            ExamResult.created_at >= day_start,
            ExamResult.created_at < day_end,
            *_countable_exam_conditions(),
        )
        .group_by(ExamResult.user_id)
    ).all()

    practice_map = {r.user_id: r for r in practice_rows}
    exam_map = {r.user_id: r for r in exam_rows}
    user_ids = set(practice_map) | set(exam_map)
    # 即使当天没有源数据，也必须清理旧聚合，避免删除/更正源数据后继续展示旧结果。
    db.execute(delete(StatsUserDaily).where(StatsUserDaily.date == date_str))
    if not user_ids:
        db.commit()
        return 0

    # 一次查询所有用户的分组，消除 N+1。
    # 归属口径：`user_groups` 优先，其次 `User.dept_group_id`（部门管理员建号/导入时只写
    # dept_group_id，没有 UserGroup 行），否则这类用户会永远显示「未分组」而从分组榜消失。
    gid_map: dict[int, int | None] = {
        r[0]: r[1]
        for r in db.execute(
            select(User.id, func.coalesce(func.min(UserGroup.group_id), User.dept_group_id))
            .select_from(User)
            .outerjoin(UserGroup, UserGroup.user_id == User.id)
            .where(User.id.in_(list(user_ids)))
            .group_by(User.id)
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
                wrong_count=int(pr.bad or 0) if pr else 0,
                exam_count=er.cnt if er else 0,
                exam_score_sum=float(er.score or 0) if er else 0,
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


def refresh_user_daily(db: Session, user_id: int, date_str: str) -> int:
    """只重算某用户某业务日的聚合行，返回写入条数。

    与 refresh_daily 同口径，但源查询限定到单个 user_id：练习作答/交卷/复核改分
    每次落库后即时刷新当日统计时，只需改动当前用户的一行，避免为一次作答重算
    全站当日聚合（管理端概览的「今日活跃」因此不再等到重启或管理员手动刷新才更新）。

    并发：与 refresh_daily 一样是「读快照 → DELETE → 重建」，因此同样要先取写锁。
    调用方常在 commit 之后调用本函数（review_service 复核改分、exam/scoring 交卷），
    此时新事务的首条 SQL 是 SELECT，若不取锁，并发作答在快照与 DELETE 之间提交的
    当日行会被抹掉并用旧快照重建，聚合长期偏低。
    """
    _acquire_write_lock(db)
    day_start, day_end = _day_bounds_utc(date_str)
    pr = db.execute(
        select(
            func.sum(func.iif(PracticeRecord.is_correct.is_not(None), 1, 0)).label("cnt"),
            func.sum(func.iif(PracticeRecord.is_correct.is_(True), 1, 0)).label("ok"),
            func.sum(func.iif(PracticeRecord.is_correct.is_(False), 1, 0)).label("bad"),
        ).where(
            PracticeRecord.user_id == user_id,
            PracticeRecord.answered_at >= day_start,
            PracticeRecord.answered_at < day_end,
        )
    ).one()
    er = db.execute(
        select(
            func.count().label("cnt"),
            func.sum(ExamResult.score).label("score"),
            func.sum(ExamResult.passed).label("pass_cnt"),
        )
        .join(ExamDefinition, ExamDefinition.id == ExamResult.exam_definition_id)
        .where(
            ExamResult.user_id == user_id,
            ExamResult.created_at >= day_start,
            ExamResult.created_at < day_end,
            *_countable_exam_conditions(),
        )
    ).one()

    # 无源数据也要清理旧聚合，避免删除/更正源数据后继续展示旧结果
    db.execute(delete(StatsUserDaily).where(StatsUserDaily.user_id == user_id, StatsUserDaily.date == date_str))
    if not pr.cnt and not er.cnt:
        db.commit()
        return 0

    group_id = db.execute(
        select(func.coalesce(func.min(UserGroup.group_id), User.dept_group_id))
        .select_from(User)
        .outerjoin(UserGroup, UserGroup.user_id == User.id)
        .where(User.id == user_id)
        .group_by(User.id)
    ).scalar()
    db.add(
        StatsUserDaily(
            user_id=user_id,
            date=date_str,
            group_id=group_id,
            answer_count=pr.cnt or 0,
            correct_count=int(pr.ok or 0),
            wrong_count=int(pr.bad or 0),
            exam_count=er.cnt or 0,
            exam_score_sum=float(er.score or 0),
            exam_pass_count=int(er.pass_cnt or 0),
        )
    )
    db.commit()
    return 1


def _dates_from_timestamps(timestamps: Iterable[str | None]) -> set[str]:
    """把 UTC ISO 时间戳换算为业务日集合；无法解析的值记录 WARNING 后跳过。

    不能静默 `continue`：时间格式一旦漂移（这正是 core/timeutil 想避免的问题），
    对应日期的聚合就永远不会被重算，而旧值会一直留在概览上且毫无痕迹。
    `refresh_user_for_timestamps` 与 `refresh_for_timestamps` 共用本函数，避免两处口径漂移。
    """
    dates: set[str] = set()
    skipped = 0
    for ts in timestamps:
        if not ts:
            continue
        try:
            parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            skipped += 1
            continue
        dates.add(_date_str(parsed))
    if skipped:
        logger.warning("[stats] %d 个时间戳无法解析，已跳过对应日期的聚合重算", skipped)
    return dates


def refresh_user_for_timestamps(db: Session, user_id: int, timestamps: Iterable[str | None]) -> int:
    """按业务日期重算单个用户的聚合行，供业务写路径即时更新统计。

    Args:
        db: 数据库会话。
        user_id: 源记录所属用户。
        timestamps: 源记录的 UTC ISO 时间戳；None/无法解析的值跳过（并记日志）。

    Returns:
        重算写入的聚合行数。
    """
    total = 0
    for date_str in sorted(_dates_from_timestamps(timestamps)):
        total += refresh_user_daily(db, user_id, date_str)
    return total


def startup_refresh(db: Session) -> None:
    """启动触发：刷新今日与昨日。"""
    today = _utcnow()
    refresh_daily(db, _date_str(today))
    refresh_daily(db, _date_str(today - timedelta(days=1)))


def refresh_for_timestamps(db: Session, timestamps: Iterable[str | None]) -> int:
    """按业务日期重算聚合，用于删除/更正源数据后清理陈旧统计。

    典型场景：管理员修改已固化考试的组卷来源，作废了该考试的成绩记录。
    若不重算，概览会在下次定时/手动刷新前继续展示已作废的分数。

    Args:
        db: 数据库会话。
        timestamps: 源记录的时间戳（UTC ISO 字符串）；None/无法解析的值跳过（并记日志）。

    Returns:
        重算写入的聚合行数合计。
    """
    total = 0
    for date_str in sorted(_dates_from_timestamps(timestamps)):
        total += refresh_daily(db, date_str)
    return total
