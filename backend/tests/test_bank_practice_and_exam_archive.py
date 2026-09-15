"""本轮整改回归测试：组卷顺序、题库练习开关、考试归档。

驱动三项 UI 整改：
- 组卷出题顺序 order_mode（bank=与导入顺序一致 / random / grouped）；
- question_banks.practice_enabled 练习开关（含「全部题库」范围也必须排除关闭的题库，
  否则用户仍能从总题库练到被关闭的题库，开关形同虚设）；
- 考试归档（已有作答记录不能删除，但可归档后对用户隐藏且保留成绩）。
"""

from __future__ import annotations

import secrets

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import hash_password
from app.database import db_session, init_db
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.question import Question, QuestionBank
from app.models.record import ExamSession, PracticeRecord
from app.models.user import User
from app.schemas.question import QuestionBankUpdate
from app.services import exam_service, practice_service, question_service
from app.services.paper_service import generate_paper


def _mk_user(role="user", **kw):
    return User(
        email=f"{secrets.token_hex(4)}@quizhub.com",
        password_hash=hash_password("pw123456"),
        name=kw.get("name", "用户"),
        role=role,
        status="active",
        email_verified=True,
    )


def _mk_bank(name="题库", enabled=True):
    return QuestionBank(name=name, group_id=None, practice_enabled=enabled)


def _mk_question(bank_id, qtype="单选题", seq_hint=0):
    """构造题目；seq_hint 仅用于生成可区分的题干。"""
    return Question(
        bank_id=bank_id,
        type=qtype,
        question=f"{qtype}-{seq_hint}",
        options=["A", "B"] if qtype in ("单选题", "多选题") else None,
        answer="A" if qtype in ("单选题", "多选题") else "正确",
        analysis="",
        difficulty=1,
        tags=[],
        score=2,
    )


# ---------- 组卷出题顺序 ----------
def test_order_mode_bank_matches_import_order():
    """默认 bank 模式：出题顺序与题库导入（入库 id）顺序一致。"""
    init_db()
    with db_session() as db:
        bank = _mk_bank()
        db.add(bank)
        db.flush()
        # 故意交错题型，模拟真实导入文件里题型混排的情况
        for i, t in enumerate(["单选题", "判断题", "单选题", "判断题", "多选题"]):
            db.add(_mk_question(bank.id, t, i))
        db.commit()
        ids = [q.id for q in db.execute(select(Question).order_by(Question.id)).scalars().all()]

        paper = generate_paper(db, {"type_quota": {"单选题": 2, "判断题": 2, "多选题": 1}, "bank_ids": [bank.id]})
        assert paper["question_ids"] == ids  # 与导入顺序完全一致
        assert paper["order_mode"] == "bank"


def test_order_mode_random_is_not_import_order():
    """random 模式：顺序不再等于入库顺序（至少与之一不同）。"""
    init_db()
    with db_session() as db:
        bank = _mk_bank()
        db.add(bank)
        db.flush()
        for i in range(12):
            db.add(_mk_question(bank.id, "单选题", i))
        db.commit()
        ids = [q.id for q in db.execute(select(Question).order_by(Question.id)).scalars().all()]

        paper = generate_paper(
            db, {"type_quota": {"单选题": 12}, "bank_ids": [bank.id], "order_mode": "random", "seed": 7}
        )
        assert sorted(paper["question_ids"]) == ids  # 集合相同
        assert paper["question_ids"] != ids  # 顺序被打乱


def test_order_mode_grouped_orders_by_canonical_type():
    """grouped 模式：按标准题型顺序分组（单选→多选→判断）。"""
    init_db()
    with db_session() as db:
        bank = _mk_bank()
        db.add(bank)
        db.flush()
        for i, t in enumerate(["判断题", "单选题", "多选题", "判断题", "单选题"]):
            db.add(_mk_question(bank.id, t, i))
        db.commit()
        paper = generate_paper(
            db,
            {
                "type_quota": {"单选题": 2, "多选题": 1, "判断题": 2},
                "bank_ids": [bank.id],
                "order_mode": "grouped",
            },
        )
        type_map = {q.id: q.type for q in db.execute(select(Question)).scalars().all()}
        types = [type_map[i] for i in paper["question_ids"]]
        assert types == ["单选题", "单选题", "多选题", "判断题", "判断题"]


def test_order_mode_invalid_rejected():
    init_db()
    with db_session() as db:
        with pytest.raises(HTTPException) as exc:
            generate_paper(db, {"type_quota": {}, "order_mode": "bogus"})
        assert exc.value.status_code == 400


# ---------- 题库练习开关 ----------
def test_practice_toggle_excludes_bank_from_list_and_total():
    """关闭题库后：范围选择器不再列出它，且「全部题库」总量也排除其题目。"""
    init_db()
    with db_session() as db:
        b1, b2 = _mk_bank("库1"), _mk_bank("库2")
        db.add_all([b1, b2])
        db.flush()
        for i in range(3):
            db.add(_mk_question(b1.id, "单选题", i))
        for i in range(4):
            db.add(_mk_question(b2.id, "单选题", i))
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()

        before = practice_service.list_modes(db, user.id)
        assert before["total"] == 7
        assert {b["id"] for b in before["banks"]} == {b1.id, b2.id}

        question_service.update_bank(db, b1.id, QuestionBankUpdate(practice_enabled=False))

        after = practice_service.list_modes(db, user.id)
        assert after["total"] == 4  # 关键：全部题库范围也排除了已关闭的库
        assert {b["id"] for b in after["banks"]} == {b2.id}


def test_practice_start_excludes_disabled_bank_even_without_bank_id():
    """未指定 bank_id 时（全部题库）也不能抽到已关闭题库的题目。"""
    init_db()
    with db_session() as db:
        b1, b2 = _mk_bank("库1"), _mk_bank("库2")
        db.add_all([b1, b2])
        db.flush()
        for i in range(3):
            db.add(_mk_question(b1.id, "单选题", i))
        for i in range(3):
            db.add(_mk_question(b2.id, "单选题", i))
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        question_service.update_bank(db, b1.id, QuestionBankUpdate(practice_enabled=False))

        rows = practice_service.start_practice(db, user.id, "sequence", None, None)
        assert len(rows) == 3
        ids = {r["id"] for r in rows}
        disabled_ids = {q.id for q in db.execute(select(Question).where(Question.bank_id == b1.id)).scalars().all()}
        assert not (ids & disabled_ids), "抽到了已关闭题库的题目"


def test_practice_start_rejects_disabled_bank_explicitly():
    """显式指定已关闭题库 -> 403（防直连 API 绕过前端隐藏）。"""
    init_db()
    with db_session() as db:
        bank = _mk_bank("库", enabled=False)
        db.add(bank)
        db.flush()
        db.add(_mk_question(bank.id, "单选题", 0))
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()

        with pytest.raises(HTTPException) as exc:
            practice_service.start_practice(db, user.id, "sequence", None, None, bank_id=bank.id)
        assert exc.value.status_code == 403


def test_home_panel_counts_exclude_disabled_bank():
    """首页「总题数」及 practiced/wrong/marked 必须排除已关闭练习的题库。

    这是本次报告的原始 BUG：关闭部分题库后，用户登录首页仍显示全部题目总数。
    同时校验分子（practiced/wrong/marked）与分母（total）口径一致，
    避免出现「已练 8 / 总题数 4」这类自相矛盾的数字。
    """
    from app.models.record import QuestionState
    from app.services import stats_service

    init_db()
    with db_session() as db:
        b1, b2 = _mk_bank("库1"), _mk_bank("库2")
        db.add_all([b1, b2])
        db.flush()
        qs1 = [_mk_question(b1.id, "单选题", i) for i in range(3)]
        qs2 = [_mk_question(b2.id, "单选题", i) for i in range(4)]
        db.add_all(qs1 + qs2)
        db.flush()
        user = _mk_user()
        db.add(user)
        db.flush()
        # 库1（将被关闭）2 题错题；库2 1 题错题且已标记
        db.add_all(
            [
                QuestionState(user_id=user.id, question_id=qs1[0].id, status="wrong", marked=True),
                QuestionState(user_id=user.id, question_id=qs1[1].id, status="wrong", marked=False),
                QuestionState(user_id=user.id, question_id=qs2[0].id, status="wrong", marked=True),
            ]
        )
        db.commit()

        before = stats_service.user_panel(db, user)
        assert before["total"] == 7 and before["practiced"] == 3

        question_service.update_bank(db, b1.id, QuestionBankUpdate(practice_enabled=False))

        after = stats_service.user_panel(db, user)
        assert after["total"] == 4, "首页总题数仍包含已关闭练习的题库"
        assert after["practiced"] == 1, "已练习数未同步排除已关闭题库"
        assert after["wrong"] == 1, "错题数未同步排除已关闭题库"
        assert after["marked"] == 1, "标记数未同步排除已关闭题库"
        assert after["practiced"] <= after["total"], "分子大于分母，首页进度会失真"


def test_practice_progress_excludes_disabled_bank():
    """顺序练习进度条 total/practiced 同样只统计开放练习的题库。"""
    from app.models.record import QuestionState

    init_db()
    with db_session() as db:
        b1, b2 = _mk_bank("库1"), _mk_bank("库2")
        db.add_all([b1, b2])
        db.flush()
        qs1 = [_mk_question(b1.id, "单选题", i) for i in range(3)]
        qs2 = [_mk_question(b2.id, "单选题", i) for i in range(4)]
        db.add_all(qs1 + qs2)
        db.flush()
        user = _mk_user()
        db.add(user)
        db.flush()
        db.add_all(
            [
                QuestionState(user_id=user.id, question_id=qs1[0].id, status="correct", marked=False),
                QuestionState(user_id=user.id, question_id=qs2[0].id, status="correct", marked=False),
            ]
        )
        db.commit()

        assert practice_service.get_progress(db, user.id) == {"total": 7, "practiced": 2}

        question_service.update_bank(db, b1.id, QuestionBankUpdate(practice_enabled=False))

        assert practice_service.get_progress(db, user.id) == {"total": 4, "practiced": 1}


def test_answer_rejects_question_from_disabled_bank():
    """直连 /practice/answer 不能作答已关闭题库的题（防绕过前端隐藏）。"""
    init_db()
    with db_session() as db:
        bank = _mk_bank("库", enabled=False)
        db.add(bank)
        db.flush()
        q = _mk_question(bank.id, "单选题", 0)
        db.add(q)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()

        with pytest.raises(HTTPException) as exc:
            practice_service.answer_question(db, user.id, q.id, "A", "sequence")
        assert exc.value.status_code == 403
        # 未写入任何练习记录
        assert db.execute(select(PracticeRecord).where(PracticeRecord.user_id == user.id)).first() is None


def test_mark_and_short_eval_reject_disabled_bank():
    """toggle-mark / short-eval 同样受题库练习开关约束。"""
    init_db()
    with db_session() as db:
        bank = _mk_bank("库", enabled=False)
        db.add(bank)
        db.flush()
        q = _mk_question(bank.id, "简答题", 0)
        db.add(q)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()

        with pytest.raises(HTTPException) as exc:
            practice_service.toggle_mark(db, user.id, q.id, True, "")
        assert exc.value.status_code == 403

        with pytest.raises(HTTPException) as exc:
            practice_service.short_eval(db, user.id, q.id, True)
        assert exc.value.status_code == 403


def test_mock_exam_excludes_disabled_bank():
    """模拟考试属练习性质，不得抽到已关闭练习的题库（管理员标注「仅考试使用」）。"""
    init_db()
    with db_session() as db:
        b1, b2 = _mk_bank("关闭库"), _mk_bank("开放库")
        db.add_all([b1, b2])
        db.flush()
        for i in range(6):
            db.add(_mk_question(b1.id, "单选题", i))
        for i in range(6):
            db.add(_mk_question(b2.id, "单选题", i))
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        question_service.update_bank(db, b1.id, QuestionBankUpdate(practice_enabled=False))

        disabled_ids = {q.id for q in db.execute(select(Question).where(Question.bank_id == b1.id)).scalars().all()}
        res = exam_service.start_mock_exam(db, user)
        served = {q.get("id") for q in res.get("questions", [])}
        assert served, "模拟考试未能出题"
        assert not (served & disabled_ids), "模拟考试抽到了已关闭练习题库的题目"


def test_formal_exam_still_uses_disabled_bank():
    """正式考试不受练习开关影响：关闭练习的题库仍可用于正式考试（回归保护）。"""
    from app.services.paper_service import generate_paper

    init_db()
    with db_session() as db:
        bank = _mk_bank("仅考试库")
        db.add(bank)
        db.flush()
        for i in range(4):
            db.add(_mk_question(bank.id, "单选题", i))
        db.commit()
        question_service.update_bank(db, bank.id, QuestionBankUpdate(practice_enabled=False))

        # 组卷（正式考试路径）仍能取到该题库题目
        paper = generate_paper(db, {"type_quota": {"单选题": 4}, "bank_ids": [bank.id]})
        assert len(paper["question_ids"]) == 4, "正式考试组卷被练习开关误伤"


def test_bank_toggle_preserves_existing_practice_records():
    init_db()
    with db_session() as db:
        from app.models.record import QuestionState

        bank = _mk_bank()
        db.add(bank)
        db.flush()
        q = _mk_question(bank.id, "单选题", 0)
        db.add(q)
        db.flush()
        user = _mk_user()
        db.add(user)
        db.flush()
        db.add(QuestionState(user_id=user.id, question_id=q.id, status="wrong", marked=True))
        db.commit()

        question_service.update_bank(db, bank.id, QuestionBankUpdate(practice_enabled=False))

        state = db.execute(select(QuestionState).where(QuestionState.user_id == user.id)).scalar_one_or_none()
        assert state is not None and state.status == "wrong", "关闭开关不应删除既有练习记录"


def test_bank_delete_blocked_when_questions_used_by_exam():
    """题库中题目被考试引用时禁止删除（保持历史考试可追溯）。"""
    init_db()
    with db_session() as db:
        creator = _mk_user("super_admin")
        db.add(creator)
        db.flush()
        bank = _mk_bank()
        db.add(bank)
        db.flush()
        q = _mk_question(bank.id, "单选题", 0)
        db.add(q)
        db.flush()
        e = ExamDefinition(
            name="引用考试", type="formal", rules={}, group_ids=None, duration_min=60,
            pass_score=60, max_attempts=0, show_score_immediately=True, show_analysis=False,
            need_review=False, status="draft", created_by=creator.id,
        )
        db.add(e)
        db.flush()
        db.add(ExamQuestion(exam_definition_id=e.id, question_id=q.id, seq=0, score=2, shuffle_map=None))
        db.commit()

        with pytest.raises(HTTPException) as exc:
            question_service.delete_bank(db, bank.id, None)
        assert exc.value.status_code == 409
        assert db.get(QuestionBank, bank.id) is not None


# ---------- 考试归档 ----------
def test_archive_exam_hides_from_users_but_keeps_records():
    """已发布且有作答记录的考试：不能删除，但可归档；归档后用户不可见、成绩保留。"""
    init_db()
    with db_session() as db:
        admin = _mk_user("super_admin")
        stu = _mk_user()
        db.add_all([admin, stu])
        db.flush()
        e = ExamDefinition(
            name="测试考试", type="formal", rules={}, group_ids=None, duration_min=60,
            pass_score=60, max_attempts=0, show_score_immediately=True, show_analysis=False,
            need_review=False, status="published", created_by=admin.id,
        )
        db.add(e)
        db.flush()
        db.add(
            ExamSession(
                exam_definition_id=e.id, user_id=stu.id, answers={}, version=1,
                started_at="2026-01-01T00:00:00", status="submitted", submitted_at="2026-01-01T01:00:00",
            )
        )
        db.commit()

        # 有作答记录 -> 不能删除
        with pytest.raises(HTTPException) as exc:
            exam_service.delete_exam(db, e.id, None)
        assert exc.value.status_code == 409

        # 可归档
        res = exam_service.archive_exam(db, e.id, None)
        assert res["status"] == "archived"

        # 用户端不可见
        visible = exam_service.list_available(db, stu)
        assert all(r["id"] != e.id for r in visible), "归档考试仍对用户可见"
        # 会话记录保留
        assert db.execute(select(ExamSession).where(ExamSession.exam_definition_id == e.id)).first() is not None


def test_unarchive_restores_visibility():
    init_db()
    with db_session() as db:
        admin = _mk_user("super_admin")
        stu = _mk_user()
        db.add_all([admin, stu])
        db.flush()
        e = ExamDefinition(
            name="归档考试", type="formal", rules={}, group_ids=None, duration_min=60,
            pass_score=60, max_attempts=0, show_score_immediately=True, show_analysis=False,
            need_review=False, status="archived", created_by=admin.id,
        )
        db.add(e)
        db.commit()

        assert all(r["id"] != e.id for r in exam_service.list_available(db, stu))
        exam_service.unarchive_exam(db, e.id, None)
        assert any(r["id"] == e.id for r in exam_service.list_available(db, stu))


def test_published_exam_without_sessions_can_be_deleted():
    """已发布但无人作答（典型为测试考试）可以直接删除。"""
    init_db()
    with db_session() as db:
        admin = _mk_user("super_admin")
        db.add(admin)
        db.flush()
        e = ExamDefinition(
            name="无人作答", type="formal", rules={}, group_ids=None, duration_min=60,
            pass_score=60, max_attempts=0, show_score_immediately=True, show_analysis=False,
            need_review=False, status="published", created_by=admin.id,
        )
        db.add(e)
        db.commit()

        exam_service.delete_exam(db, e.id, None)
        assert db.get(ExamDefinition, e.id) is None


def test_dept_admin_cannot_archive_foreign_exam():
    """归档同样受部门 scope 约束（防越权操作他人部门的考试）。"""
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
        admin = _mk_user("dept_admin", dept_group_id=rd.id)
        db.add(admin)
        db.flush()
        e = ExamDefinition(
            name="市场部考试", type="formal", rules={}, group_ids=[mk.id], duration_min=60,
            pass_score=60, max_attempts=0, show_score_immediately=True, show_analysis=False,
            need_review=False, status="published", created_by=admin.id,
        )
        db.add(e)
        db.commit()

        with pytest.raises(HTTPException) as exc:
            exam_service.archive_exam(db, e.id, {rd.id})
        assert exc.value.status_code == 403
