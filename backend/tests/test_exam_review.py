"""考试结算与复核的并发/边界测试。

驱动 backend-code-review Critical 1 与 Suggestion 4/6/8 的修复：
- submit_answer 原子乐观锁：同 version 双提交一成一 409；
- submit_exam 幂等：重复交卷不重复结算；
- review 原子自增成绩：并发复核不丢分；
- review partial_score 超出题目分值被拒；
- 正式考试超时交卷计 0 分。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.security import hash_password
from app.database import db_session, init_db
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult, ExamSession, ShortAnswerReview
from app.models.user import User
from app.services import exam_service, review_service


def _seed_exam(db, qtype="单选题", score=2.0, end_at=None, need_review=False, status="published"):
    """建一个用户 + 一场考试 + 固化题目，返回 (user, exam_def, session, eqs)。"""
    user = User(
        email=f"stu{secrets.token_hex(4)}@quizhub.com",
        password_hash=hash_password("pw123456"),
        name="考生",
        role="user",
        status="active",
        email_verified=True,
    )
    db.add(user)
    db.flush()
    e = ExamDefinition(
        name="测试考试",
        type="formal",
        rules={},
        group_ids=None,
        start_at=None,
        end_at=end_at,
        duration_min=60,
        pass_score=60,
        max_attempts=0,
        show_score_immediately=True,
        show_analysis=False,
        need_review=need_review,
        status=status,
        created_by=user.id,
    )
    db.add(e)
    db.flush()
    bank = QuestionBank(name="测试库")
    db.add(bank)
    db.flush()
    q = Question(
        bank_id=bank.id,
        type=qtype,
        question="题",
        options=["A", "B"],
        answer="A",
        analysis="",
        difficulty=1,
        tags=[],
        score=score,
    )
    db.add(q)
    db.flush()
    db.add(ExamQuestion(exam_definition_id=e.id, question_id=q.id, seq=0, score=score, shuffle_map=None))
    db.commit()
    return user, e, q


def test_submit_answer_same_version_one_succeeds_one_conflict():
    init_db()
    with db_session() as db:
        user, e, q = _seed_exam(db)
        # 开考
        sess = exam_service.start_exam(db, user, e.id)
        sid = sess["session_id"]
        v0 = sess["version"]
        # 第一次提交（v0→v1）成功
        r1 = exam_service.submit_answer(db, user, sid, q.id, "A", v0)
        assert r1["version"] == v0 + 1
        # 用同一 v0 再提交 → 应 409
        from fastapi import HTTPException

        try:
            exam_service.submit_answer(db, user, sid, q.id, "B", v0)
            assert False, "同 version 重复提交应冲突"
        except HTTPException as exc:
            assert exc.status_code == 409


def test_submit_answer_correct_version_chain():
    init_db()
    with db_session() as db:
        user, e, q = _seed_exam(db)
        sess = exam_service.start_exam(db, user, e.id)
        sid = sess["session_id"]
        v = sess["version"]
        # 顺序提交三次，version 递增
        for _ in range(3):
            r = exam_service.submit_answer(db, user, sid, q.id, "A", v)
            v = r["version"]
        assert v == sess["version"] + 3


def test_submit_exam_idempotent_no_double_scoring():
    init_db()
    with db_session() as db:
        user, e, q = _seed_exam(db)
        sess = exam_service.start_exam(db, user, e.id)
        sid = sess["session_id"]
        # 作答正确（2 分）
        exam_service.submit_answer(db, user, sid, q.id, "A", sess["version"])
        # 第一次交卷
        res1 = exam_service.submit_exam(db, user, sid)
        assert res1["need_review"] is False
        score1 = res1["score"]
        # 重复交卷：不应抛错也不应重复结算
        res2 = exam_service.submit_exam(db, user, sid)
        # 第二次返回的分数应与第一次一致（幂等）
        assert res2["score"] == score1
        # 数据库只应有一条成绩
        count = len(db.execute(select(ExamResult).where(ExamResult.exam_session_id == sid)).all())
        assert count == 1


def test_review_atomic_increment_no_loss():
    init_db()
    with db_session() as db:
        user, e, q = _seed_exam(db, qtype="简答题", score=5.0, need_review=True)
        sess = exam_service.start_exam(db, user, e.id)
        sid = sess["session_id"]
        exam_service.submit_answer(db, user, sid, q.id, "我的简答", sess["version"])
        res = exam_service.submit_exam(db, user, sid)
        assert res["need_review"] is True
        # 此时客观分应为 0（唯一一题是简答）
        result = db.execute(select(ExamResult).where(ExamResult.exam_session_id == sid)).scalar_one()
        assert result.score == 0.0
        review = db.execute(select(ShortAnswerReview).where(ShortAnswerReview.exam_session_id == sid)).scalar_one()
        # 复核 pass → 原子自增 5 分
        reviewer = User(
            email=f"rev{secrets.token_hex(4)}@quizhub.com",
            password_hash="x",
            name="复核员",
            role="super_admin",
            status="active",
            email_verified=True,
        )
        db.add(reviewer)
        db.commit()
        review_service.review(db, review.id, "pass", None, reviewer)
        db.refresh(result)
        assert result.score == 5.0


def test_review_partial_score_out_of_range_rejected():
    init_db()
    with db_session() as db:
        user, e, q = _seed_exam(db, qtype="简答题", score=5.0, need_review=True)
        sess = exam_service.start_exam(db, user, e.id)
        sid = sess["session_id"]
        exam_service.submit_answer(db, user, sid, q.id, "答", sess["version"])
        exam_service.submit_exam(db, user, sid)
        review = db.execute(select(ShortAnswerReview).where(ShortAnswerReview.exam_session_id == sid)).scalar_one()
        reviewer = User(
            email=f"rev{secrets.token_hex(4)}@quizhub.com",
            password_hash="x",
            name="复核员",
            role="super_admin",
            status="active",
            email_verified=True,
        )
        db.add(reviewer)
        db.commit()
        from fastapi import HTTPException

        # partial_score 超过题目分值 5 → 拒绝
        try:
            review_service.review(db, review.id, "partial", 6.0, reviewer)
            assert False, "partial_score 超分应被拒"
        except HTTPException as exc:
            assert exc.status_code == 400
        # 负分也应被拒
        try:
            review_service.review(db, review.id, "partial", -1.0, reviewer)
            assert False, "负 partial_score 应被拒"
        except HTTPException as exc:
            assert exc.status_code == 400
        # 合法 partial（3 分）通过
        review_service.review(db, review.id, "partial", 3.0, reviewer)
        result = db.execute(select(ExamResult).where(ExamResult.exam_session_id == sid)).scalar_one()
        db.refresh(result)
        assert result.score == 3.0


def test_review_partial_requires_partial_score():
    init_db()
    with db_session() as db:
        user, e, q = _seed_exam(db, qtype="简答题", score=5.0, need_review=True)
        sess = exam_service.start_exam(db, user, e.id)
        sid = sess["session_id"]
        exam_service.submit_answer(db, user, sid, q.id, "答", sess["version"])
        exam_service.submit_exam(db, user, sid)
        review = db.execute(select(ShortAnswerReview).where(ShortAnswerReview.exam_session_id == sid)).scalar_one()
        reviewer = User(
            email=f"rev{secrets.token_hex(4)}@quizhub.com",
            password_hash="x",
            name="复核员",
            role="super_admin",
            status="active",
            email_verified=True,
        )
        db.add(reviewer)
        db.commit()
        from fastapi import HTTPException

        # verdict=partial 但不传 partial_score → 拒绝
        try:
            review_service.review(db, review.id, "partial", None, reviewer)
            assert False, "partial 必须提供 partial_score"
        except HTTPException as exc:
            assert exc.status_code == 400


def test_formal_exam_overtime_scores_zero():
    init_db()
    with db_session() as db:
        # 先以未来 end_at 建考试，使 start_exam 能在时段内开考
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        user, e, q = _seed_exam(db, score=10.0, end_at=future, status="published")
        sess = exam_service.start_exam(db, user, e.id)
        sid = sess["session_id"]
        # 作答正确
        exam_service.submit_answer(db, user, sid, q.id, "A", sess["version"])
        # 开考后把 end_at 改到过去，模拟交卷时已超时
        e.end_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.commit()
        res = exam_service.submit_exam(db, user, sid)
        # 超时 → 客观题不计分
        assert res["overtime"] is True
        assert res["score"] == 0.0
        assert res["passed"] is False


def test_formal_exam_within_time_scores_correctly():
    init_db()
    with db_session() as db:
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        user, e, q = _seed_exam(db, score=10.0, end_at=future, status="published")
        sess = exam_service.start_exam(db, user, e.id)
        sid = sess["session_id"]
        exam_service.submit_answer(db, user, sid, q.id, "A", sess["version"])
        res = exam_service.submit_exam(db, user, sid)
        assert res.get("overtime") is False
        assert res["score"] == 10.0


def test_count_attempts_excludes_scoring():
    init_db()
    with db_session() as db:
        user, e, q = _seed_exam(db)
        # 手造一个 scoring 会话（模拟崩溃残留）
        s = ExamSession(
            exam_definition_id=e.id,
            user_id=user.id,
            status="scoring",
            answers={},
            version=1,
            started_at=datetime.now(timezone.utc).isoformat(),
            submitted_at=datetime.now(timezone.utc).isoformat(),
            remaining_sec=60,
        )
        db.add(s)
        db.commit()
        n = exam_service._count_attempts(db, e.id, user.id)
        # scoring 不应计入尝试次数
        assert n == 0


def test_recover_stuck_scoring_resets_session():
    init_db()
    with db_session() as db:
        user, e, q = _seed_exam(db)
        # 一个 30 分钟前的 scoring 会话（无成绩记录 → 崩溃残留）
        old = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat()
        s = ExamSession(
            exam_definition_id=e.id,
            user_id=user.id,
            status="scoring",
            answers={},
            version=1,
            started_at=old,
            submitted_at=old,
            remaining_sec=60,
        )
        db.add(s)
        db.commit()
        recovered = exam_service._recover_stuck_scoring(db)
        assert recovered == 1
        db.refresh(s)
        assert s.status == "in_progress"


def test_recover_stuck_scoring_preserves_pending_review_session():
    """含 ExamResult 的 scoring 会话是合法待复核状态，不可被回收（Critical B3 回归）。"""
    init_db()
    with db_session() as db:
        user, e, q = _seed_exam(db, qtype="简答题", score=5.0, need_review=True)
        sess = exam_service.start_exam(db, user, e.id)
        sid = sess["session_id"]
        exam_service.submit_answer(db, user, sid, q.id, "我的简答", sess["version"])
        res = exam_service.submit_exam(db, user, sid)
        assert res["need_review"] is True
        # 此时 session 状态为 scoring，且有 ExamResult（合法待复核）
        session = db.execute(select(ExamSession).where(ExamSession.id == sid)).scalar_one()
        assert session.status == "scoring"
        # 把 submitted_at 改到 40 分钟前，模拟超过回收阈值
        session.submitted_at = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat()
        db.commit()
        recovered = exam_service._recover_stuck_scoring(db)
        assert recovered == 0  # 含 result 的会话不被回收
        db.refresh(session)
        assert session.status == "scoring"  # 仍保持待复核状态


def test_review_concurrent_no_double_increment():
    """并发对同一简答复核：条件 UPDATE 保证只有一方加分，杜绝分数重复自增（Critical B2 回归）。

    用线程并发模拟：两位复核人对同一 review_id 同时提交 pass，各自独立 Session
    （复刻真实请求：每请求一个 SessionLocal）。
    """
    import threading

    from fastapi import HTTPException

    from app.database import SessionLocal

    init_db()
    review_id = None
    with SessionLocal() as db:
        user, e, q = _seed_exam(db, qtype="简答题", score=5.0, need_review=True)
        sess = exam_service.start_exam(db, user, e.id)
        sid = sess["session_id"]
        exam_service.submit_answer(db, user, sid, q.id, "我的简答", sess["version"])
        exam_service.submit_exam(db, user, sid)
        review = db.execute(select(ShortAnswerReview).where(ShortAnswerReview.exam_session_id == sid)).scalar_one()
        reviewer = User(
            email="rev@quizhub.com",
            password_hash="x",
            name="复核员",
            role="super_admin",
            status="active",
            email_verified=True,
        )
        db.add(reviewer)
        db.commit()
        review_id = review.id

    results = {"ok": 0, "conflict": 0}

    def _do_review():
        with SessionLocal() as db:
            rev = db.execute(select(User).where(User.email == "rev@quizhub.com")).scalar_one()
            try:
                review_service.review(db, review_id, "pass", None, rev)
                results["ok"] += 1
            except HTTPException as exc:
                if exc.status_code == 400:
                    results["conflict"] += 1

    threads = [threading.Thread(target=_do_review) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 恰好一次成功加分，另一次被拒
    assert results["ok"] == 1, f"应只有 1 次成功复核，实际 {results['ok']}"
    assert results["conflict"] == 1, f"应只有 1 次冲突，实际 {results['conflict']}"

    review_session_id = review.exam_session_id
    with SessionLocal() as db:
        result = db.execute(select(ExamResult).where(ExamResult.exam_session_id == review_session_id)).scalar_one()
        db.refresh(result)
        # 5 分题 pass，仅加一次 → score=5，而非 10
        assert result.score == 5.0, f"分数应只加一次=5，实际 {result.score}"


def test_overtime_exam_stays_failed_after_publish():
    """超时交卷的成绩，复核公布后仍判不及格（Critical B4 回归）。"""
    init_db()
    with db_session() as db:
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        user, e, q = _seed_exam(db, qtype="简答题", score=60.0, end_at=future, status="published", need_review=True)
        sess = exam_service.start_exam(db, user, e.id)
        sid = sess["session_id"]
        exam_service.submit_answer(db, user, sid, q.id, "超时作答", sess["version"])
        # 让交卷判定为超时
        e.end_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.commit()
        res = exam_service.submit_exam(db, user, sid)
        assert res["overtime"] is True
        # result.overtime 应为 True
        result = db.execute(select(ExamResult).where(ExamResult.exam_session_id == sid)).scalar_one()
        db.refresh(result)
        assert result.overtime is True
        # 复核给满分后公布
        reviewer = User(
            email=f"rev{secrets.token_hex(4)}@quizhub.com",
            password_hash="x",
            name="复核员",
            role="super_admin",
            status="active",
            email_verified=True,
        )
        db.add(reviewer)
        db.commit()
        review = db.execute(select(ShortAnswerReview).where(ShortAnswerReview.exam_session_id == sid)).scalar_one()
        review_service.review(db, review.id, "pass", None, reviewer)
        published = review_service.publish_results(db, e.id)
        assert published["published"] >= 1
        db.refresh(result)
        # 满分但超时 → 仍不及格
        assert result.score == 60.0
        assert result.passed is False, "超时考试复核后不应判及格"


def test_review_double_review_rejected():
    init_db()
    with db_session() as db:
        user, e, q = _seed_exam(db, qtype="简答题", score=5.0, need_review=True)
        sess = exam_service.start_exam(db, user, e.id)
        sid = sess["session_id"]
        exam_service.submit_answer(db, user, sid, q.id, "答", sess["version"])
        exam_service.submit_exam(db, user, sid)
        review = db.execute(select(ShortAnswerReview).where(ShortAnswerReview.exam_session_id == sid)).scalar_one()
        reviewer = User(
            email=f"rev{secrets.token_hex(4)}@quizhub.com",
            password_hash="x",
            name="复核员",
            role="super_admin",
            status="active",
            email_verified=True,
        )
        db.add(reviewer)
        db.commit()
        review_service.review(db, review.id, "pass", None, reviewer)
        from fastapi import HTTPException

        try:
            review_service.review(db, review.id, "pass", None, reviewer)
            assert False, "重复复核应被拒"
        except HTTPException as exc:
            assert exc.status_code == 400


def test_review_out_of_scope_rejected():
    """dept_admin 不得经直接 POST /admin/review/{id} 复核其数据范围外的简答（IDOR 防护回归）。

    list_pending 按 scope 过滤只能挡住"列表里看不到"，但 review_id 是自增整数可枚举；
    review() 必须对归属考生做 user_in_scope 校验，否则 dept_admin 可给任意部门考生改分。
    """
    from app.core.deps import subtree_ids
    from app.models.group import Group

    init_db()
    with db_session() as db:
        user, e, q = _seed_exam(db, qtype="简答题", score=5.0, need_review=True)
        sess = exam_service.start_exam(db, user, e.id)
        sid = sess["session_id"]
        exam_service.submit_answer(db, user, sid, q.id, "答", sess["version"])
        exam_service.submit_exam(db, user, sid)
        review = db.execute(select(ShortAnswerReview).where(ShortAnswerReview.exam_session_id == sid)).scalar_one()
        reviewer = User(
            email=f"rev{secrets.token_hex(4)}@quizhub.com",
            password_hash="x",
            name="复核员",
            role="super_admin",
            status="active",
            email_verified=True,
        )
        db.add(reviewer)
        db.commit()
        # 构造一个不含考生的 scope：单独建一个分组，subtree_ids 只含它自身
        other = Group(name="其他部门", type="部门")
        db.add(other)
        db.commit()
        scope = subtree_ids(db, other.id)  # 仅含 other，不含考生任何分组
        from fastapi import HTTPException

        try:
            review_service.review(db, review.id, "pass", None, reviewer, scope)
            assert False, "范围外复核应被拒"
        except HTTPException as exc:
            assert exc.status_code == 403
        # super_admin（scope=None）下应放行
        review_service.review(db, review.id, "pass", None, reviewer, None)


def test_submit_exam_uses_latest_committed_answers():
    """submit_exam 必须以锁定时的 DB 当前 answers 判分，而非函数入口的内存快照。

    复刻窗口：T0 交卷请求读到 answers={} 快照 → T1 滞后的 submit_answer 提交并 commit
    （status 仍 in_progress，version 不变）→ T2 交卷请求锁定 scoring 成功。若用 T0
    快照判分，T1 的作答会丢失（计 0 分）；修复后应读到 T1 的答案、判满分。
    """
    init_db()
    with db_session() as db:
        user, e, q = _seed_exam(db, score=10.0, status="published")
        sid_obj = exam_service.start_exam(db, user, e.id)
        sid, v0 = sid_obj["session_id"], sid_obj["version"]
        uid, qid = user.id, q.id

    from app.database import SessionLocal
    from app.models.record import ExamSession

    db_main = SessionLocal()
    db_other = SessionLocal()
    try:
        u_main = db_main.get(User, uid)
        u_other = db_other.get(User, uid)
        # 主请求读到快照（空）
        assert db_main.get(ExamSession, sid).answers == {}
        # 滞后提交作答（正确答案 A）
        exam_service.submit_answer(db_other, u_other, sid, qid, "A", v0)
        db_other.commit()
        # 主请求走交卷：应读到滞后提交的答案并判 10 分，而非用空快照判 0 分
        res = exam_service.submit_exam(db_main, u_main, sid)
    finally:
        db_main.close()
        db_other.close()
    assert res["overtime"] is False
    assert res["correct_count"] == 1
    assert res["score"] == 10.0, "滞后提交的作答应被计入判分，而非丢失于过期快照"
