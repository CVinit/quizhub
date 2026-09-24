"""题库/题目服务层 CRUD 与删除保护回归。

重点覆盖 E2：`delete_question` 必须与 `delete_bank` 一样，拦截「已被考试引用」的题目——
否则 `exam_questions.question_id ON DELETE CASCADE` 会把该题从已固化（含已发布/进行中）
考试里静默抽走，使同一份考卷的 total_score 随交卷时间变化、历史成绩失去题目引用。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.errors import DomainError
from app.database import db_session, init_db
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.group import Group
from app.models.question import Question, QuestionBank, QuestionTag
from app.models.record import PracticeRecord, QuestionState
from app.models.user import User
from app.schemas.question import (
    QuestionBankCreate,
    QuestionBankUpdate,
    QuestionCreate,
    QuestionUpdate,
)
from app.services import question_service


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mk_group(db, name: str = "研发部") -> Group:
    g = Group(name=name, type="部门")
    db.add(g)
    db.flush()
    return g


def _mk_bank(db, name: str = "网络题库", group_id: int | None = None) -> QuestionBank:
    b = QuestionBank(name=name, group_id=group_id, practice_enabled=True)
    db.add(b)
    db.flush()
    return b


def _mk_single_choice(db, bank: QuestionBank, text: str = "HTTP 默认端口?", group_id: int | None = None) -> Question:
    q = Question(
        bank_id=bank.id,
        type="单选题",
        question=text,
        options=["21", "80"],
        answer="B",
        analysis="",
        difficulty=2,
        tags=[],
        score=2,
        group_id=group_id,
    )
    db.add(q)
    db.flush()
    return q


def _mk_user(db, email: str = "student@example.com") -> User:
    u = User(
        email=email,
        password_hash="x",
        name="学员",
        role="user",
        status="active",
        email_verified=True,
    )
    db.add(u)
    db.flush()
    return u


def _mk_exam(db, *, created_by: int | None = None) -> ExamDefinition:
    e = ExamDefinition(
        name="考试",
        type="formal",
        rules={},
        group_ids=None,
        duration_min=60,
        pass_score=60,
        max_attempts=0,
        show_score_immediately=True,
        show_analysis=False,
        need_review=False,
        status="published",
        created_by=created_by,
    )
    db.add(e)
    db.flush()
    return e


# ---------- 题库 ----------
def test_bank_crud_and_practice_toggle():
    init_db()
    with db_session() as db:
        created = question_service.create_bank(db, QuestionBankCreate(name="网络", practice_enabled=True), None)
        assert created.id

        question_service.update_bank(db, created.id, QuestionBankUpdate(name="网络2"), None)
        updated = question_service.update_bank(db, created.id, QuestionBankUpdate(practice_enabled=False), None)
        assert updated.name == "网络2"
        assert updated.practice_enabled is False

        # 列表：practice_enabled 省略返回全部，显式过滤按开关
        assert [b["name"] for b in question_service.list_banks(db, None, None)] == ["网络2"]
        assert question_service.list_banks(db, None, True) == []
        assert [b["id"] for b in question_service.list_banks(db, None, False)] == [created.id]

        question_service.delete_bank(db, created.id, None)
        assert db.get(QuestionBank, created.id) is None


def test_bank_scope_rejects_out_of_range_group():
    init_db()
    with db_session() as db:
        own = _mk_group(db, "本部门")
        other = _mk_group(db, "其他部门")
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            question_service.create_bank(db, QuestionBankCreate(name="越权库", group_id=other.id), {own.id})
        assert exc.value.status_code == 403

        with pytest.raises((DomainError, HTTPException)) as missing:
            question_service.create_bank(db, QuestionBankCreate(name="幽灵库", group_id=99999), None)
        assert missing.value.status_code == 400


def test_delete_bank_rejected_when_question_referenced_by_exam():
    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        q = _mk_single_choice(db, bank)
        exam = _mk_exam(db)
        db.add(ExamQuestion(exam_definition_id=exam.id, question_id=q.id, seq=0, score=2))
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            question_service.delete_bank(db, bank.id, None)
        assert exc.value.status_code == 409
        assert db.get(QuestionBank, bank.id) is not None


def test_delete_bank_cleans_questions_and_practice_data():
    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        q = _mk_single_choice(db, bank)
        student = _mk_user(db)
        db.add(
            PracticeRecord(
                user_id=student.id,
                question_id=q.id,
                bank_id=bank.id,
                mode="sequence",
                user_answer={"a": 1},
                is_correct=True,
                answered_at=_now(),
            )
        )
        db.add(QuestionState(user_id=student.id, question_id=q.id, status="correct", marked=False, marked_note=""))
        db.commit()
        qid, bank_id = q.id, bank.id

        question_service.delete_bank(db, bank_id, None)

        assert db.get(Question, qid) is None
        assert db.get(QuestionBank, bank_id) is None
        # 删库必须连带清掉引用这些题目的练习记录与掌握度：原用例种了这两类数据却从不断言，
        # 清理逻辑被删掉也发现不了（与单题删除的 test_delete_question_cleans_practice_data 同口径）
        assert (
            db.execute(
                select(func.count()).select_from(PracticeRecord).where(PracticeRecord.question_id == qid)
            ).scalar_one()
            == 0
        )
        assert (
            db.execute(
                select(func.count()).select_from(QuestionState).where(QuestionState.question_id == qid)
            ).scalar_one()
            == 0
        )


# ---------- 题目创建校验 ----------
@pytest.mark.parametrize(
    ("qtype", "answer"),
    [
        ("单选题", ""),
        ("判断题", "A"),
        ("填空题", []),
        ("填空题", [[]]),
        ("简答题", "   "),
        ("拖拽题", {}),
        ("不存在的题型", "A"),
    ],
)
def test_create_question_rejects_invalid_type_or_answer(qtype, answer):
    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        payload = QuestionCreate(type=qtype, question="题干", answer=answer, bank_id=bank.id)
        with pytest.raises((DomainError, HTTPException)) as exc:
            question_service.create_question(db, payload, None)
        assert exc.value.status_code == 400


def test_create_question_requires_group_match_with_bank():
    init_db()
    with db_session() as db:
        own = _mk_group(db, "本部门")
        other = _mk_group(db, "其他部门")
        bank = _mk_bank(db, group_id=own.id)
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            question_service.create_question(
                db,
                QuestionCreate(
                    type="单选题", question="题干", options=["a"], answer="A", bank_id=bank.id, group_id=other.id
                ),
                None,
            )
        assert exc.value.status_code == 400


def test_create_question_registers_tags_and_persists():
    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        q = question_service.create_question(
            db,
            QuestionCreate(
                type="多选题",
                question="以下哪些是关系型数据库?",
                options=["MySQL", "Redis"],
                answer="A",
                bank_id=bank.id,
                tags=["数据库", "基础"],
                score=3,
            ),
            None,
        )
        assert q.id
        names = {t.name for t in db.execute(select(QuestionTag)).scalars().all()}
        assert {"数据库", "基础"} <= names


# ---------- 题目更新 ----------
class _StubPayload:
    """伪造 schema 载荷：用于验证服务层白名单（不信赖 Pydantic 字段列表）。"""

    def __init__(self, data: dict) -> None:
        self._data = data

    def model_dump(self, **_kwargs) -> dict:
        return dict(self._data)


def test_update_question_rejects_non_whitelisted_field():
    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        q = _mk_single_choice(db, bank)
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            question_service.update_question(db, q.id, _StubPayload({"id": 12345}), None)
        assert exc.value.status_code == 400
        assert "不允许修改" in exc.value.detail


def test_update_question_type_change_revalidates_existing_answer():
    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        q = _mk_single_choice(db, bank)
        db.commit()

        # 单选题答案 "B" 不是合法判断题答案 → 400，避免留下不可判分的题
        with pytest.raises((DomainError, HTTPException)) as exc:
            question_service.update_question(db, q.id, QuestionUpdate(type="判断题"), None)
        assert exc.value.status_code == 400

        # 同时改题型与答案 → 放行
        updated = question_service.update_question(db, q.id, QuestionUpdate(type="判断题", answer="正确"), None)
        assert updated.type == "判断题"
        assert updated.answer == "正确"


def test_update_question_rejects_unknown_type_and_missing_question():
    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        q = _mk_single_choice(db, bank)
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            question_service.update_question(db, q.id, QuestionUpdate(type="外星题"), None)
        assert exc.value.status_code == 400

        with pytest.raises((DomainError, HTTPException)) as missing:
            question_service.update_question(db, 999999, QuestionUpdate(score=3), None)
        assert missing.value.status_code == 404


# ---------- 题目删除保护（E2 回归）----------
def test_delete_question_rejected_when_referenced_by_exam():
    """E2 回归：被考试固化的题目不得删除，否则卷面被静默抽题。"""
    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        q = _mk_single_choice(db, bank)
        exam = _mk_exam(db)
        db.add(ExamQuestion(exam_definition_id=exam.id, question_id=q.id, seq=0, score=2))
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            question_service.delete_question(db, q.id, None)
        assert exc.value.status_code == 409

        # 题目与固化行都必须保留
        assert db.get(Question, q.id) is not None
        assert (
            db.execute(
                select(func.count()).select_from(ExamQuestion).where(ExamQuestion.exam_definition_id == exam.id)
            ).scalar_one()
            == 1
        )


def test_delete_question_cleans_practice_records_and_states():
    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        q = _mk_single_choice(db, bank)
        student = _mk_user(db)
        db.add(
            PracticeRecord(
                user_id=student.id,
                question_id=q.id,
                bank_id=bank.id,
                mode="sequence",
                user_answer=None,
                is_correct=False,
                answered_at=_now(),
            )
        )
        db.add(QuestionState(user_id=student.id, question_id=q.id, status="wrong", marked=True, marked_note=""))
        db.commit()
        qid = q.id

        question_service.delete_question(db, qid, None)

        assert db.get(Question, qid) is None
        assert (
            db.execute(
                select(func.count()).select_from(PracticeRecord).where(PracticeRecord.question_id == qid)
            ).scalar_one()
            == 0
        )
        assert (
            db.execute(
                select(func.count()).select_from(QuestionState).where(QuestionState.question_id == qid)
            ).scalar_one()
            == 0
        )


def test_delete_question_scope_and_missing():
    init_db()
    with db_session() as db:
        own = _mk_group(db, "本部门")
        bank = _mk_bank(db, group_id=own.id)
        q = _mk_single_choice(db, bank, group_id=own.id)
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            question_service.delete_question(db, q.id, set())
        assert exc.value.status_code == 403

        with pytest.raises((DomainError, HTTPException)) as missing:
            question_service.delete_question(db, 999999, None)
        assert missing.value.status_code == 404


# ---------- 列表 / 统计 ----------
def test_list_questions_filters_and_escapes_like_wildcards():
    init_db()
    with db_session() as db:
        bank = _mk_bank(db)
        q_under = _mk_single_choice(db, bank, text="a_b 题")
        q_plain = _mk_single_choice(db, bank, text="axb 题")
        q_other = _mk_single_choice(db, bank, text="别的题")
        db.commit()

        # 未转义时 "_" 会匹配任意单字符，把 axb/别的题 一并返回
        rows, total = question_service.list_questions(db, keyword="_")
        assert total == 1
        assert [r.id for r in rows] == [q_under.id]

        rows, total = question_service.list_questions(db, type_="单选题", bank_id=bank.id, difficulty=2)
        assert total == 3
        assert {r.id for r in rows} == {q_under.id, q_plain.id, q_other.id}

        rows, total = question_service.list_questions(db, keyword="axb")
        assert [r.id for r in rows] == [q_plain.id]


def test_type_stats_by_bank_and_tags():
    init_db()
    with db_session() as db:
        b1 = _mk_bank(db, "库1")
        b2 = _mk_bank(db, "库2")
        q1 = _mk_single_choice(db, b1, text="网络题")
        q1.tags = ["网络"]
        q2 = _mk_single_choice(db, b2, text="数据库题")
        q2.tags = ["数据库"]
        db.commit()

        all_stats = question_service.type_stats(db)
        assert all_stats["单选题"] == 2

        only_b1 = question_service.type_stats(db, bank_ids=[b1.id])
        assert only_b1["单选题"] == 1

        by_tag = question_service.type_stats(db, tags=["数据库"])
        assert by_tag["单选题"] == 1

        assert question_service.type_stats(db, bank_ids=[99999])["单选题"] == 0


def test_list_questions_scope_limits_rows():
    init_db()
    with db_session() as db:
        own = _mk_group(db, "本部门")
        other = _mk_group(db, "其他部门")
        b_own = _mk_bank(db, "本部门库", group_id=own.id)
        b_other = _mk_bank(db, "其他部门库", group_id=other.id)
        _mk_single_choice(db, b_own, text="本部门题", group_id=own.id)
        _mk_single_choice(db, b_other, text="其他部门题", group_id=other.id)
        db.commit()

        rows, total = question_service.list_questions(db, scope={own.id})
        assert total == 1
        assert rows[0].question == "本部门题"
