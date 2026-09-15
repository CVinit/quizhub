"""后台列表「状态」下拉筛选的回归测试。

驱动四个管理页新增的状态筛选（题库管理 / 正式考试 / 考试记录 / 简答复核）：
筛选一律在后端完成，且必须保证：
- 省略参数时返回全部（向后兼容，管理端不能因新增筛选而丢数据）；
- 传入参数时精确生效；
- 非法状态值不应放行未过滤的全量数据（简复仇判分支）。
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import hash_password
from app.database import db_session, init_db
from app.models.exam import ExamDefinition
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult, ShortAnswerReview
from app.models.user import User
from app.services import exam_service, question_service, review_service


def _mk_user(role="super_admin"):
    return User(
        email=f"{secrets.token_hex(4)}@quizhub.com",
        password_hash=hash_password("pw123456"),
        name="用户",
        role=role,
        status="active",
        email_verified=True,
    )


def _mk_exam(creator_id: int, name: str, status: str) -> ExamDefinition:
    return ExamDefinition(
        name=name,
        type="formal",
        rules={},
        group_ids=None,
        duration_min=60,
        pass_score=60,
        max_attempts=0,
        show_score_immediately=True,
        show_analysis=False,
        need_review=False,
        status=status,
        created_by=creator_id,
    )


# ---------- 题库管理：开放练习 / 仅考试使用 ----------
def test_bank_list_status_filter():
    init_db()
    with db_session() as db:
        b1, b2 = (
            QuestionBank(name="开放库", practice_enabled=True),
            QuestionBank(name="仅考试库", practice_enabled=False),
        )
        db.add_all([b1, b2])
        db.commit()

        # 省略参数：全部（管理端默认需看到并管理已关闭的题库）
        assert {b["name"] for b in question_service.list_banks(db, None, None)} == {"开放库", "仅考试库"}
        assert [b["name"] for b in question_service.list_banks(db, None, True)] == ["开放库"]
        assert [b["name"] for b in question_service.list_banks(db, None, False)] == ["仅考试库"]


# ---------- 正式考试：按状态筛选（含已归档）----------
def test_exam_list_status_filter():
    init_db()
    with db_session() as db:
        admin = _mk_user()
        db.add(admin)
        db.flush()
        db.add_all(
            [
                _mk_exam(admin.id, "草稿考试", "draft"),
                _mk_exam(admin.id, "已发布考试", "published"),
                _mk_exam(admin.id, "已归档考试", "archived"),
            ]
        )
        db.commit()

        # 省略参数：全部（含已归档），保证管理端不丢数据
        assert len(exam_service.list_exams(db, None, None)) == 3
        assert [e["name"] for e in exam_service.list_exams(db, None, "draft")] == ["草稿考试"]
        assert [e["name"] for e in exam_service.list_exams(db, None, "published")] == ["已发布考试"]
        assert [e["name"] for e in exam_service.list_exams(db, None, "archived")] == ["已归档考试"]
        # 无匹配状态返回空列表而非全量
        assert exam_service.list_exams(db, None, "reviewing") == []


def test_exam_list_status_filter_keeps_dept_scope():
    """状态筛选不得绕过部门 scope 隔离。"""
    from app.models.group import Group

    init_db()
    with db_session() as db:
        root = Group(name="集团", type="部门")
        db.add(root)
        db.flush()
        rd = Group(name="研发部", type="部门", parent_id=root.id)
        mk = Group(name="市场部", type="部门", parent_id=root.id)
        db.add_all([rd, mk])
        db.flush()
        admin = _mk_user("dept_admin")
        db.add(admin)
        db.flush()
        own = _mk_exam(admin.id, "本部门考试", "published")
        own.group_ids = [rd.id]
        other = _mk_exam(admin.id, "他部门考试", "published")
        other.group_ids = [mk.id]
        db.add_all([own, other])
        db.commit()

        names = [e["name"] for e in exam_service.list_exams(db, {rd.id}, "published")]
        assert names == ["本部门考试"], "状态筛选绕过了部门 scope"


# ---------- 考试记录：及格 / 待复核 / 已公布 ----------
def test_exam_results_outcome_filter():
    init_db()
    with db_session() as db:
        admin = _mk_user()
        student = _mk_user("user")
        db.add_all([admin, student])
        db.flush()
        exam = _mk_exam(admin.id, "成绩考试", "published")
        db.add(exam)
        db.flush()
        db.add_all(
            [
                ExamResult(
                    exam_definition_id=exam.id,
                    user_id=student.id,
                    score=90,
                    total_score=100,
                    passed=True,
                    published=True,
                    need_review=False,
                ),
                ExamResult(
                    exam_definition_id=exam.id,
                    user_id=student.id,
                    score=30,
                    total_score=100,
                    passed=False,
                    published=True,
                    need_review=False,
                ),
                ExamResult(
                    exam_definition_id=exam.id,
                    user_id=student.id,
                    score=0,
                    total_score=100,
                    passed=False,
                    published=False,
                    need_review=True,
                ),
            ]
        )
        db.commit()

        assert len(exam_service.list_results(db, exam.id, None, 500, None)) == 3
        assert len(exam_service.list_results(db, exam.id, None, 500, "passed")) == 1
        assert len(exam_service.list_results(db, exam.id, None, 500, "failed")) == 2
        assert len(exam_service.list_results(db, exam.id, None, 500, "pending")) == 1
        assert len(exam_service.list_results(db, exam.id, None, 500, "published")) == 2
        # 待复核与已公布互斥
        pending = exam_service.list_results(db, exam.id, None, 500, "pending")
        assert all(r["published"] is False for r in pending)


# ---------- 简答复核：待复核 / 已复核历史 ----------
def _mk_review(db, exam_result_id: int, user_id: int, question_id: int, verdict: str | None):
    r = ShortAnswerReview(
        exam_result_id=exam_result_id,
        user_id=user_id,
        question_id=question_id,
        user_answer="考生作答",
        reference_answer="参考答案",
        verdict=verdict,
    )
    db.add(r)
    return r


def test_review_list_verdict_filter():
    init_db()
    with db_session() as db:
        admin = _mk_user()
        student = _mk_user("user")
        db.add_all([admin, student])
        db.flush()
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        q = Question(
            bank_id=bank.id,
            type="简答题",
            question="简答",
            options=None,
            answer="参考答案",
            analysis="",
            difficulty=1,
            tags=[],
            score=2,
        )
        db.add(q)
        db.flush()
        exam = _mk_exam(admin.id, "复核考试", "reviewing")
        db.add(exam)
        db.flush()
        result = ExamResult(
            exam_definition_id=exam.id,
            user_id=student.id,
            score=0,
            total_score=100,
            passed=False,
            published=False,
            need_review=True,
        )
        db.add(result)
        db.flush()
        _mk_review(db, result.id, student.id, q.id, None)
        _mk_review(db, result.id, student.id, q.id, "pass")
        _mk_review(db, result.id, student.id, q.id, "fail")
        db.commit()

        # 省略 / pending 都只看待复核（保持既有默认行为）
        assert len(review_service.list_pending(db, None, 500, None)) == 1
        assert len(review_service.list_pending(db, None, 500, "pending")) == 1
        assert len(review_service.list_pending(db, None, 500, "done")) == 2
        assert len(review_service.list_pending(db, None, 500, "pass")) == 1
        assert len(review_service.list_pending(db, None, 500, "fail")) == 1
        assert review_service.list_pending(db, None, 500, "partial") == []
        # 已复核项需带上结论字段，供前端只读展示
        done = review_service.list_pending(db, None, 500, "done")
        assert all(r["verdict"] in ("pass", "fail") for r in done)


def test_review_still_rejects_double_review():
    """已复核项不可重复复核（新增筛选后此约束不变）。"""
    init_db()
    with db_session() as db:
        admin = _mk_user()
        student = _mk_user("user")
        db.add_all([admin, student])
        db.flush()
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        q = Question(
            bank_id=bank.id,
            type="简答题",
            question="简答",
            options=None,
            answer="参考",
            analysis="",
            difficulty=1,
            tags=[],
            score=2,
        )
        db.add(q)
        db.flush()
        exam = _mk_exam(admin.id, "复核考试2", "reviewing")
        db.add(exam)
        db.flush()
        result = ExamResult(
            exam_definition_id=exam.id,
            user_id=student.id,
            score=0,
            total_score=100,
            passed=False,
            published=False,
            need_review=True,
        )
        db.add(result)
        db.flush()
        r = _mk_review(db, result.id, student.id, q.id, "pass")
        db.commit()

        try:
            review_service.review(db, r.id, "fail", None, admin, None)
            raise AssertionError("已复核项被重复复核")
        except HTTPException as exc:
            assert exc.status_code == 400


def test_review_list_verdict_unknown_value_returns_empty():
    """未知 verdict 不应退化为「返回全部」，避免前端传错值时误以为在看待复核。"""
    init_db()
    with db_session() as db:
        admin = _mk_user()
        student = _mk_user("user")
        db.add_all([admin, student])
        db.flush()
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        q = Question(
            bank_id=bank.id,
            type="简答题",
            question="简答",
            options=None,
            answer="参考",
            analysis="",
            difficulty=1,
            tags=[],
            score=2,
        )
        db.add(q)
        db.flush()
        exam = _mk_exam(admin.id, "复核考试3", "reviewing")
        db.add(exam)
        db.flush()
        result = ExamResult(
            exam_definition_id=exam.id,
            user_id=student.id,
            score=0,
            total_score=100,
            passed=False,
            published=False,
            need_review=True,
        )
        db.add(result)
        db.flush()
        _mk_review(db, result.id, student.id, q.id, "pass")
        db.commit()

        assert review_service.list_pending(db, None, 500, "bogus") == []
        assert db.execute(select(ShortAnswerReview)).scalars().all()
