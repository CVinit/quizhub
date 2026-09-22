"""试卷模板服务测试（拆分后 `exam/templates.py` 的独立覆盖）。

模板接口此前只被管理端路由间接覆盖，删除保护（被考试引用 → 409）与
范围过滤（部门管理员不得引用范围外题目）缺少直接断言。
"""

from __future__ import annotations

import secrets

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.errors import DomainError
from app.core.security import hash_password
from app.database import db_session, init_db
from app.models.exam import ExamDefinition, PaperTemplate
from app.models.group import Group
from app.models.question import Question, QuestionBank
from app.models.user import User
from app.schemas.exam import PaperTemplateIn
from app.services import exam_service


def _mk_user(db, role: str = "super_admin") -> User:
    user = User(
        email=f"{secrets.token_hex(4)}@example.com",
        password_hash=hash_password("pw123456"),
        name="管理员",
        role=role,
        status="active",
        email_verified=True,
    )
    db.add(user)
    db.flush()
    return user


def _seed_questions(db, *, group_id: int | None = None, count: int = 4) -> QuestionBank:
    bank = QuestionBank(name="库", group_id=group_id, practice_enabled=True)
    db.add(bank)
    db.flush()
    for i in range(count):
        db.add(
            Question(
                bank_id=bank.id,
                type="单选题",
                question=f"题{i}",
                options=["甲", "乙"],
                answer="A",
                analysis="",
                difficulty=1,
                tags=[],
                score=2,
                group_id=group_id,
            )
        )
    db.flush()
    return bank


def _config(quota: int = 2) -> dict:
    return {"type_quota": {"单选题": quota}, "max_questions": 10}


def test_preview_paper_returns_question_details_without_persisting():
    init_db()
    with db_session() as db:
        _seed_questions(db, count=3)
        db.commit()

        preview = exam_service.preview_paper(db, _config(2), None)

        assert preview["count"] == 2
        assert len(preview["question_ids"]) == 2
        assert len(preview["questions"]) == 2
        assert preview["questions"][0]["score"] == 2
        assert preview["questions"][0]["question"]
        assert preview["total_score"] == 4
        # 预览不落库
        assert db.execute(select(func.count()).select_from(PaperTemplate)).scalar_one() == 0


def test_preview_paper_applies_data_scope():
    init_db()
    with db_session() as db:
        own = Group(name="本部门", type="部门")
        other = Group(name="其他部门", type="部门")
        db.add_all([own, other])
        db.flush()
        _seed_questions(db, group_id=own.id, count=2)
        _seed_questions(db, group_id=other.id, count=2)
        db.commit()

        # 范围限定时只抽得到范围内的题
        preview = exam_service.preview_paper(db, _config(4), {own.id})
        assert preview["count"] == 2


def test_template_crud_and_delete_guard():
    init_db()
    with db_session() as db:
        admin = _mk_user(db)
        _seed_questions(db, count=3)
        db.commit()

        created = exam_service.create_template(
            db, PaperTemplateIn(name="期末模板", mode="formal", config=_config(2)), admin, None
        )
        assert created["count"] == 2
        template_id = created["id"]

        templates = exam_service.list_templates(db)
        assert len(templates) == 1
        assert templates[0]["name"] == "期末模板"
        assert templates[0]["question_count"] == 2
        assert templates[0]["config"]["type_quota"] == {"单选题": 2}

        # 未被引用 → 可删除
        exam_service.delete_template(db, template_id)
        assert db.get(PaperTemplate, template_id) is None

        # 不存在 → 404
        with pytest.raises((DomainError, HTTPException)) as missing:
            exam_service.delete_template(db, 999999)
        assert missing.value.status_code == 404


def test_delete_template_rejected_when_referenced_by_exam():
    init_db()
    with db_session() as db:
        admin = _mk_user(db)
        _seed_questions(db, count=2)
        db.commit()

        created = exam_service.create_template(
            db, PaperTemplateIn(name="引用中", mode="formal", config=_config(2)), admin, None
        )
        exam = ExamDefinition(
            name="引用模板的考试",
            type="formal",
            paper_template_id=created["id"],
            rules={},
            group_ids=None,
            duration_min=60,
            pass_score=60,
            max_attempts=0,
            show_score_immediately=True,
            show_analysis=False,
            need_review=False,
            status="draft",
            created_by=admin.id,
        )
        db.add(exam)
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as in_use:
            exam_service.delete_template(db, created["id"])
        assert in_use.value.status_code == 409
        assert "无法删除" in in_use.value.detail
        assert db.get(PaperTemplate, created["id"]) is not None
