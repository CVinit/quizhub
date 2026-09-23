"""用户面板与管理端概览指标。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.question import Question
from app.models.record import ExamResult, PracticeRecord, QuestionState
from app.models.stats import StatsUserDaily
from app.models.user import User
from app.services.exam_service import exam_in_scope
from app.services.stats.common import _date_str, _utcnow


# ---------- 用户面板 ----------
def user_panel(db: Session, user: User) -> dict:
    # 总题数只统计「开放练习」的题库：与练习入口口径一致。
    # 否则关闭某题库练习后，首页仍显示包含它的总数，用户进入练习却发现题量对不上。
    from app.services.practice_service import enabled_bank_ids

    enabled = enabled_bank_ids(db)
    total_q = db.execute(select(func.count(Question.id)).where(Question.bank_id.in_(enabled))).scalar() or 0
    # 用 SQL 聚合替代全量载入 QuestionState 后 Python 计数。
    # JOIN Question 并限定同一批开放题库：QuestionState 无 bank_id，
    # 不过滤会让 practiced/wrong/marked 把已关闭题库的题算进来，
    # 与 total 口径不一致（首页出现「已练 8 / 总题数 4」这类矛盾数字）。
    row = db.execute(
        select(
            func.sum(func.iif(QuestionState.status != "unanswered", 1, 0)).label("practiced"),
            func.sum(func.iif(QuestionState.status.in_(("correct", "mastered")), 1, 0)).label("correct"),
            func.sum(func.iif(QuestionState.status == "wrong", 1, 0)).label("wrong"),
            func.sum(func.iif(QuestionState.marked.is_(True), 1, 0)).label("marked"),
        )
        .join(Question, Question.id == QuestionState.question_id)
        .where(QuestionState.user_id == user.id, Question.bank_id.in_(enabled))
    ).one()
    practiced = int(row.practiced or 0)
    correct = int(row.correct or 0)
    wrong = int(row.wrong or 0)
    marked = int(row.marked or 0)
    accuracy = round(correct / practiced * 100) if practiced else 0

    # 最近 5 次考试，一次 JOIN 取出考试名与类型（前端需按类型区分展示）。
    from app.models.exam import ExamDefinition

    results = db.execute(
        select(ExamResult, ExamDefinition.name, ExamDefinition.type)
        .join(ExamDefinition, ExamDefinition.id == ExamResult.exam_definition_id)
        .where(ExamResult.user_id == user.id)
        .order_by(ExamResult.id.desc())
        .limit(5)
    ).all()
    recent_exams = []
    for r, exam_name, exam_type in results:
        recent_exams.append(
            {
                "name": exam_name,
                "type": exam_type,
                "session_id": r.exam_session_id,
                "score": r.score,
                "total_score": r.total_score,
                "passed": r.passed,
                "published": r.published,
            }
        )

    # 模拟考试平均分（百分制）：仅统计本人已发布的模拟考试成绩。
    # 归一化为百分制，便于与正式考试成绩比较（各场满分不同）。
    # 未发布（含简答待复核）与超时成绩不计入，口径与"练习性、即时出分"一致。
    mock_row = db.execute(
        select(
            func.avg(ExamResult.score * 100.0 / func.nullif(ExamResult.total_score, 0)),
            func.count(ExamResult.id),
        )
        .join(ExamDefinition, ExamDefinition.id == ExamResult.exam_definition_id)
        .where(
            ExamResult.user_id == user.id,
            ExamDefinition.type == "mock",
            ExamResult.published.is_(True),
        )
    ).one()
    mock_avg = round(float(mock_row[0]), 1) if mock_row[0] is not None else None
    mock_count = int(mock_row[1] or 0)
    return {
        "total": total_q,
        "practiced": practiced,
        "correct": correct,
        "wrong": wrong,
        "marked": marked,
        "accuracy": accuracy,
        "mock_avg_score": mock_avg,
        "mock_attempts": mock_count,
        "recent_exams": recent_exams,
    }


# ---------- 管理端概览 ----------
def admin_overview(db: Session, scope: set[int] | None = None) -> dict:
    """管理端概览指标。

    scope 非 None（部门管理员）时用户/练习/复核/今日活跃均限定在其数据范围内；
    范围以 SQL 子查询表达，避免把整个部门的用户 id 物化进 `IN (...)`。
    `total_questions` 为全站题库口径：题库属全站共享资源，不按部门切分
    （与 user_panel 只统计开放练习题库的口径不同，此处显式声明）。
    super_admin（scope=None）返回全站指标。
    """
    from app.core.deps import user_ids_subquery
    from app.models.exam import ExamDefinition
    from app.models.record import ShortAnswerReview

    scoped = user_ids_subquery(scope) if scope is not None else None

    if scoped is None:
        total_users = db.execute(select(func.count(User.id))).scalar() or 0
        active_users = db.execute(select(func.count(User.id)).where(User.status == "active")).scalar() or 0
        pending_approvals = db.execute(select(func.count(User.id)).where(User.status == "pending")).scalar() or 0
    else:
        total_users = db.execute(select(func.count()).select_from(scoped.subquery())).scalar() or 0
        active_users = (
            db.execute(
                select(func.count()).select_from(User).where(User.id.in_(scoped), User.status == "active")
            ).scalar()
            or 0
        )
        pending_approvals = (
            db.execute(
                select(func.count()).select_from(User).where(User.id.in_(scoped), User.status == "pending")
            ).scalar()
            or 0
        )

    total_questions = db.execute(select(func.count(Question.id))).scalar() or 0

    # 完成率：范围内用户已练过的题目数 / 全站总题数
    practiced_stmt = select(func.count(func.distinct(QuestionState.question_id))).where(
        QuestionState.status != "unanswered"
    )
    if scoped is not None:
        practiced_stmt = practiced_stmt.where(QuestionState.user_id.in_(scoped))
    practiced_q = db.execute(practiced_stmt).scalar() or 0
    completion_rate = round(practiced_q / total_questions * 100) if total_questions else 0

    # 正确率：按可见用户的练习记录统计
    ans_stmt = select(func.count(PracticeRecord.id))
    correct_stmt = select(func.count(PracticeRecord.id)).where(PracticeRecord.is_correct.is_(True))
    if scoped is not None:
        ans_stmt = ans_stmt.where(PracticeRecord.user_id.in_(scoped))
        correct_stmt = correct_stmt.where(PracticeRecord.user_id.in_(scoped))
    total_answers = db.execute(ans_stmt).scalar() or 0
    correct_answers = db.execute(correct_stmt).scalar() or 0
    accuracy = round(correct_answers / total_answers * 100) if total_answers else 0

    # 正式考试数：与考试列表 / _check_exam_scope 共用 exam_in_scope（**子集**口径）。
    # 原实现用「有交集」判定：共同指派给多个部门的考试会被算进某个部门的概览，
    # 但该部门管理员既看不到它（列表按子集过滤）也改不了（操作前校验 403），
    # 概览数字与其可操作范围自相矛盾。
    if scope is None:
        total_exams = (
            db.execute(select(func.count(ExamDefinition.id)).where(ExamDefinition.type == "formal")).scalar() or 0
        )
    else:
        exam_rows = db.execute(select(ExamDefinition.group_ids).where(ExamDefinition.type == "formal")).all()
        total_exams = sum(1 for (gids,) in exam_rows if exam_in_scope(gids, scope))

    # 待复核简答：按可见用户过滤
    review_stmt = select(func.count(ShortAnswerReview.id)).where(ShortAnswerReview.verdict.is_(None))
    if scoped is not None:
        review_stmt = review_stmt.where(ShortAnswerReview.user_id.in_(scoped))
    pending_reviews = db.execute(review_stmt).scalar() or 0

    # 今日活跃用户（今日有预聚合记录）
    today = _date_str(_utcnow())
    today_stmt = select(func.count(func.distinct(StatsUserDaily.user_id))).where(StatsUserDaily.date == today)
    if scoped is not None:
        today_stmt = today_stmt.where(StatsUserDaily.user_id.in_(scoped))
    today_active = db.execute(today_stmt).scalar() or 0

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
