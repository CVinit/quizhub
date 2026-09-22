"""分组服务与练习服务的回归测试。

`group_service`（38%）与 `practice_service`（56%）的目标：补齐增删改守卫
（成环、有子分组/题库/题目）、范围裁剪，以及练习判分/标记/自评/进度各分支。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.errors import DomainError
from app.database import db_session, init_db
from app.models.group import Group, UserGroup
from app.models.question import Question, QuestionBank
from app.models.record import QuestionState
from app.models.user import User
from app.schemas.group import GroupCreate, GroupUpdate
from app.services import group_service, practice_service


def _mk_user(db, email: str = "u@example.com") -> User:
    user = User(email=email, password_hash="x", name="学员", role="user", status="active", email_verified=True)
    db.add(user)
    db.flush()
    return user


def _mk_bank(db, name: str, *, enabled: bool = True, group_id: int | None = None) -> QuestionBank:
    bank = QuestionBank(name=name, group_id=group_id, practice_enabled=enabled)
    db.add(bank)
    db.flush()
    return bank


def _mk_question(db, bank: QuestionBank, *, qtype: str = "单选题", answer="A", text: str = "题") -> Question:
    question = Question(
        bank_id=bank.id,
        type=qtype,
        question=text,
        options=["甲", "乙"] if qtype in ("单选题", "多选题") else None,
        answer=answer,
        analysis="解析",
        difficulty=2,
        tags=[],
        score=2,
    )
    db.add(question)
    db.flush()
    return question


# ---------- 分组服务 ----------
def test_group_create_update_and_type_validation():
    init_db()
    with db_session() as db:
        root = group_service.create_group(db, GroupCreate(name="总部", type="部门"))
        assert root.id

        renamed = group_service.update_group(db, root.id, GroupUpdate(name="集团", sort=5))
        assert renamed.name == "集团"
        assert renamed.sort == 5

        with pytest.raises((DomainError, HTTPException)) as bad_type:
            group_service.create_group(db, GroupCreate(name="x", type="外星类型"))
        assert bad_type.value.status_code == 400

        with pytest.raises((DomainError, HTTPException)) as missing_parent:
            group_service.create_group(db, GroupCreate(name="x", parent_id=99999))
        assert missing_parent.value.status_code == 400

        with pytest.raises((DomainError, HTTPException)) as bad_update_type:
            group_service.update_group(db, root.id, GroupUpdate(type="外星类型"))
        assert bad_update_type.value.status_code == 400

        with pytest.raises((DomainError, HTTPException)) as missing:
            group_service.update_group(db, 99999, GroupUpdate(name="x"))
        assert missing.value.status_code == 404


def test_group_update_cycle_and_self_parent_guards():
    init_db()
    with db_session() as db:
        a = group_service.create_group(db, GroupCreate(name="A"))
        b = group_service.create_group(db, GroupCreate(name="B", parent_id=a.id))
        c = group_service.create_group(db, GroupCreate(name="C", parent_id=b.id))

        with pytest.raises((DomainError, HTTPException)) as self_parent:
            group_service.update_group(db, a.id, GroupUpdate(parent_id=a.id))
        assert self_parent.value.status_code == 400

        # A 挂到自己的后代 C 之下 → 成环
        with pytest.raises((DomainError, HTTPException)) as cycle:
            group_service.update_group(db, a.id, GroupUpdate(parent_id=c.id))
        assert cycle.value.status_code == 400

        with pytest.raises((DomainError, HTTPException)) as ghost:
            group_service.update_group(db, a.id, GroupUpdate(parent_id=99999))
        assert ghost.value.status_code == 400


def test_group_delete_guards_and_relink_cleanup():
    init_db()
    with db_session() as db:
        parent = group_service.create_group(db, GroupCreate(name="父"))
        child = group_service.create_group(db, GroupCreate(name="子", parent_id=parent.id))
        student = _mk_user(db)
        db.add(UserGroup(user_id=student.id, group_id=child.id))
        db.commit()

        # 有子分组 → 拒绝
        with pytest.raises((DomainError, HTTPException)) as has_child:
            group_service.delete_group(db, parent.id)
        assert has_child.value.status_code == 400

        # 有题库 → 拒绝
        _mk_bank(db, "题库", group_id=child.id)
        db.commit()
        with pytest.raises((DomainError, HTTPException)) as has_bank:
            group_service.delete_group(db, child.id)
        assert has_bank.value.status_code == 400

        # 只有题目（无题库） → 拒绝
        orphan_bank = _mk_bank(db, "另一库", group_id=None)
        q = _mk_question(db, orphan_bank)
        q.group_id = child.id
        db.commit()
        with pytest.raises((DomainError, HTTPException)) as has_question:
            group_service.delete_group(db, child.id)
        assert has_question.value.status_code == 400

        # 清掉引用后可删除，并解除用户关联
        q.group_id = None
        db.query(QuestionBank).filter(QuestionBank.group_id == child.id).update({"group_id": None})
        db.commit()
        group_service.delete_group(db, child.id)
        assert db.get(Group, child.id) is None
        assert db.query(UserGroup).filter(UserGroup.group_id == child.id).count() == 0

        with pytest.raises((DomainError, HTTPException)) as missing:
            group_service.delete_group(db, 99999)
        assert missing.value.status_code == 404


def test_build_tree_scopes_and_reparents_out_of_scope_parent():
    init_db()
    with db_session() as db:
        a = group_service.create_group(db, GroupCreate(name="A"))
        b = group_service.create_group(db, GroupCreate(name="B", parent_id=a.id))
        group_service.create_group(db, GroupCreate(name="C"))
        db.commit()

        full = group_service.build_tree(db, None)
        assert {n["name"] for n in full} == {"A", "C"}
        node_a = next(n for n in full if n["name"] == "A")
        assert [child["name"] for child in node_a["children"]] == ["B"]

        scoped = group_service.build_tree(db, {b.id})
        # B 的父 A 不在范围内 → B 作为子树根返回，不泄露 A
        assert len(scoped) == 1
        assert scoped[0]["name"] == "B"
        assert scoped[0]["parent_id"] is None


# ---------- 练习服务 ----------
def test_list_modes_counts_only_enabled_banks():
    init_db()
    with db_session() as db:
        open_bank = _mk_bank(db, "开放库")
        closed_bank = _mk_bank(db, "仅考试库", enabled=False)
        user = _mk_user(db)
        q_open = _mk_question(db, open_bank)
        q_closed = _mk_question(db, closed_bank)
        db.add(QuestionState(user_id=user.id, question_id=q_open.id, status="correct", marked=True, marked_note=""))
        db.add(QuestionState(user_id=user.id, question_id=q_closed.id, status="wrong", marked=False, marked_note=""))
        db.commit()

        modes = practice_service.list_modes(db, user.id)
        assert modes["total"] == 1
        assert modes["practiced"] == 1
        assert modes["marked"] == 1
        assert [b["name"] for b in modes["banks"]] == ["开放库"]
        assert modes["type_dist"] == {"单选题": 1}

        # 指定关闭的题库 → 403（防止经统计接口泄露题量）
        with pytest.raises((DomainError, HTTPException)) as closed:
            practice_service.list_modes(db, user.id, closed_bank.id)
        assert closed.value.status_code == 403


def test_start_practice_modes_and_guards():
    init_db()
    with db_session() as db:
        open_bank = _mk_bank(db, "开放库")
        closed_bank = _mk_bank(db, "仅考试库", enabled=False)
        user = _mk_user(db)
        q1 = _mk_question(db, open_bank, text="题1")
        _mk_question(db, open_bank, qtype="判断题", answer="正确", text="题2")
        q_closed = _mk_question(db, closed_bank, text="关闭题")
        db.add(QuestionState(user_id=user.id, question_id=q1.id, status="wrong", marked=True, marked_note="重点复习"))
        db.commit()

        assert len(practice_service.start_practice(db, user.id, "sequence", None, 10)) == 2
        assert len(practice_service.start_practice(db, user.id, "random", None, 10)) == 2
        assert len(practice_service.start_practice(db, user.id, "bank", None, 10)) == 2
        assert len(practice_service.start_practice(db, user.id, "type", "单选题", 10)) == 1
        assert [q["id"] for q in practice_service.start_practice(db, user.id, "wrong", None, 10)] == [q1.id]
        assert [q["id"] for q in practice_service.start_practice(db, user.id, "mark", None, 10)] == [q1.id]
        # 标记状态必须随题返回：缺失会让前端的「标记/取消标记」退化成单向开关、备注不可见
        marked_rows = practice_service.start_practice(db, user.id, "mark", None, 10)
        assert marked_rows[0]["marked"] is True
        assert marked_rows[0]["marked_note"] == "重点复习"
        # 未标记的同范围题目必须回落到「未标记、无备注」，不能串到别的题上
        sequence_rows = practice_service.start_practice(db, user.id, "sequence", None, 10)
        unmarked = next(r for r in sequence_rows if r["id"] != q1.id)
        assert unmarked["marked"] is False and unmarked["marked_note"] == ""
        # limit 超过上限时被钳制，而不是无界返回
        assert len(practice_service.start_practice(db, user.id, "sequence", None, 10**9)) == 2
        # 指定题库范围
        assert len(practice_service.start_practice(db, user.id, "sequence", None, 10, open_bank.id)) == 2

        with pytest.raises((DomainError, HTTPException)) as bad_mode:
            practice_service.start_practice(db, user.id, "nope", None, 10)
        assert bad_mode.value.status_code == 400

        with pytest.raises((DomainError, HTTPException)) as no_type:
            practice_service.start_practice(db, user.id, "type", None, 10)
        assert no_type.value.status_code == 400

        with pytest.raises((DomainError, HTTPException)) as closed:
            practice_service.start_practice(db, user.id, "sequence", None, 10, closed_bank.id)
        assert closed.value.status_code == 403
        assert q_closed.id  # 关闭题库的题不可达


def test_answer_question_grading_and_state_transitions():
    init_db()
    with db_session() as db:
        bank = _mk_bank(db, "开放库")
        user = _mk_user(db)
        choice = _mk_question(db, bank, text="选择题")
        judge = _mk_question(db, bank, qtype="判断题", answer="正确", text="判断题")
        short = _mk_question(db, bank, qtype="简答题", answer="参考答案", text="简答题")
        db.commit()

        correct = practice_service.answer_question(db, user.id, choice.id, "A", "sequence")
        assert correct["is_correct"] is True
        assert correct["analysis"] == "解析"

        wrong = practice_service.answer_question(db, user.id, judge.id, "错误", "sequence")
        assert wrong["is_correct"] is False

        pending = practice_service.answer_question(db, user.id, short.id, "我的回答", "sequence")
        assert pending["is_correct"] is None
        assert pending["reference_answer"] == "参考答案"

        states = {
            s.question_id: s.status for s in db.query(QuestionState).filter(QuestionState.user_id == user.id).all()
        }
        assert states[choice.id] == "correct"
        assert states[judge.id] == "wrong"
        assert states[short.id] == "unanswered"  # 简答等自评


def test_answer_question_guards_closed_and_missing_and_orphan():
    init_db()
    with db_session() as db:
        closed = _mk_bank(db, "仅考试库", enabled=False)
        user = _mk_user(db)
        q_closed = _mk_question(db, closed)
        # bank_id 为空的孤儿题目不属于任何开放题库
        orphan_bank = _mk_bank(db, "临时库")
        orphan = _mk_question(db, orphan_bank)
        orphan.bank_id = None
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as closed_exc:
            practice_service.answer_question(db, user.id, q_closed.id, "A", "sequence")
        assert closed_exc.value.status_code == 403

        with pytest.raises((DomainError, HTTPException)) as orphan_exc:
            practice_service.answer_question(db, user.id, orphan.id, "A", "sequence")
        assert orphan_exc.value.status_code == 403

        with pytest.raises((DomainError, HTTPException)) as missing:
            practice_service.answer_question(db, user.id, 999999, "A", "sequence")
        assert missing.value.status_code == 404


def test_short_eval_flow_and_guards():
    init_db()
    with db_session() as db:
        bank = _mk_bank(db, "开放库")
        user = _mk_user(db)
        short = _mk_question(db, bank, qtype="简答题", answer="参考", text="简答")
        choice = _mk_question(db, bank, text="选择题")
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as not_short:
            practice_service.short_eval(db, user.id, choice.id, True)
        assert not_short.value.status_code == 400

        with pytest.raises((DomainError, HTTPException)) as no_record:
            practice_service.short_eval(db, user.id, short.id, True)
        assert no_record.value.status_code == 400

        practice_service.answer_question(db, user.id, short.id, "作答", "sequence")
        practice_service.short_eval(db, user.id, short.id, True)
        state = (
            db.query(QuestionState)
            .filter(QuestionState.user_id == user.id, QuestionState.question_id == short.id)
            .one()
        )
        assert state.status == "correct"

        with pytest.raises((DomainError, HTTPException)) as again:
            practice_service.short_eval(db, user.id, short.id, False)
        assert again.value.status_code == 400

        # 需复习 → wrong
        practice_service.answer_question(db, user.id, short.id, "再答", "sequence")
        # 已自评的是最近一条记录，需先重置该记录以避免"已自评"分支
        from app.models.record import PracticeRecord

        latest = (
            db.query(PracticeRecord)
            .filter(PracticeRecord.user_id == user.id, PracticeRecord.question_id == short.id)
            .order_by(PracticeRecord.id.desc())
            .first()
        )
        latest.self_eval = None
        db.commit()
        practice_service.short_eval(db, user.id, short.id, False)
        db.refresh(state)
        assert state.status == "wrong"


def test_toggle_mark_progress_and_recent():
    init_db()
    with db_session() as db:
        bank = _mk_bank(db, "开放库")
        user = _mk_user(db)
        q = _mk_question(db, bank, text="一道比较长的题目" * 10)
        db.commit()

        practice_service.toggle_mark(db, user.id, q.id, True, "重点")
        state = (
            db.query(QuestionState).filter(QuestionState.user_id == user.id, QuestionState.question_id == q.id).one()
        )
        assert state.marked is True
        assert state.marked_note == "重点"

        practice_service.toggle_mark(db, user.id, q.id, False, "")
        db.refresh(state)
        assert state.marked is False

        practice_service.answer_question(db, user.id, q.id, "A", "sequence")
        assert practice_service.get_progress(db, user.id) == {"total": 1, "practiced": 1}

        recent = practice_service.recent_practice(db, user.id)
        assert len(recent) == 1
        assert recent[0]["question"].endswith("…")  # 长题干截断
        assert recent[0]["type"] == "单选题"
