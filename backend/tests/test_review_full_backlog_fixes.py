"""本轮（suggestions + nits 全量清理）的回归测试。

覆盖：
- A1 索引迁移（补齐缺失索引、名称与模型声明一致）
- A2 审计日志来源 IP（请求上下文）
- A3 越权拒绝留日志
- A4 删除分组清理考试/模板的悬空 JSON 指派
- A5 练习作答体积上限
- C1 `need_review` 开关生效
- C2 超时成绩的未公布原因
- C4 畸形 xlsx → ValueError（路由转 400 而非 500）
- C5 `parse_workbook` 暴露完整解析结果（消除二次解析）
- C6 判分边界（0 分题、浮点压线、全角归一、单选答案形状）
- C7 统计重算跳过坏时间戳时记日志
- N3 `create_user` 返回 User
- N9 导入失败文案受控（不透传驱动原文）
- N10 题目/题库响应 schema 与前端契约一致
"""

from __future__ import annotations

import importlib.util
import logging
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.errors import DomainError
from app.core.security import create_access_token, hash_password
from app.database import SessionLocal, db_session, get_db, init_db
from app.main import create_app
from app.models.exam import ExamDefinition, PaperTemplate
from app.models.group import Group
from app.models.question import Question, QuestionBank
from app.models.record import ExamSession
from app.models.system import AuditLog
from app.models.user import User
from app.schemas.record import PracticeAnswerIn
from app.services import (
    audit_service,
    exam_service,
    grading,
    group_service,
    paper_service,
    question_service,
    user_service,
)

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


# ---------- 公共工具 ----------
def _mk_user(db, *, role: str = "super_admin", dept_group_id: int | None = None) -> User:
    user = User(
        email=f"{secrets.token_hex(4)}@example.com",
        password_hash=hash_password("pw123456"),
        name="用户",
        role=role,
        status="active",
        email_verified=True,
        dept_group_id=dept_group_id,
    )
    db.add(user)
    db.flush()
    return user


def _load_migration(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, _SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api(tmp_path, monkeypatch):
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    monkeypatch.setattr("app.config.FILES_DIR", files_dir)
    monkeypatch.setattr("app.api.system.FILES_DIR", files_dir)
    app = create_app()

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client


def _admin_headers() -> dict[str, str]:
    with SessionLocal() as db:
        admin = _mk_user(db)
        db.commit()
        token = create_access_token(admin.id, {"role": admin.role, "ver": admin.token_version})
    return {"Authorization": f"Bearer {token}"}


# ---------- A1 索引迁移 ----------
def test_index_migration_covers_all_model_indexes():
    """迁移里列的索引名必须与模型 create_all 生成的完全一致（防两套 schema 漂移）。"""
    migration = _load_migration("migrate_2026_09_19.py", "migrate_2026_09_19_names")
    init_db()
    from app.config import DB_PATH

    conn = sqlite3.connect(str(DB_PATH))
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()

    missing = [name for name, _t, _c in migration._INDEXES if name not in existing]
    assert not missing, f"迁移声明的索引未由模型生成（名称不一致）：{missing}"


def test_index_migration_recreates_missing_indexes():
    """存量库缺少这些索引时，迁移必须补齐且可重复执行。"""
    migration = _load_migration("migrate_2026_09_19.py", "migrate_2026_09_19_create")
    init_db()
    from app.config import DB_PATH

    conn = sqlite3.connect(str(DB_PATH))
    for name, _table, _col in migration._INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    conn.commit()
    assert not any(
        conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
        for name, _t, _c in migration._INDEXES
    ), "前提：索引已被删除"

    created = migration.create_missing_indexes(conn, dry_run=False)
    conn.commit()
    assert len(created) == len(migration._INDEXES)
    for name, _table, _col in migration._INDEXES:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone(), name

    assert migration.create_missing_indexes(conn, dry_run=False) == [], "必须幂等"
    conn.close()


# ---------- A2 审计来源 IP ----------
def test_audit_log_picks_up_request_ip():
    init_db()
    from app.core.request_context import set_request_ip

    with db_session() as db:
        set_request_ip("203.0.113.7")
        audit_service.log(db, None, "test.action", "thing", 1)

    with db_session() as db:
        row = db.execute(select(AuditLog).where(AuditLog.action == "test.action")).scalar_one()
    assert row.ip == "203.0.113.7", "审计行必须带上请求上下文里的来源 IP"

    # 无请求上下文（脚本/后台任务）时保持为空，且不报错
    with db_session() as db:
        set_request_ip("")
        audit_service.log(db, None, "test.action.bg", "thing", 2)
    with db_session() as db:
        assert db.execute(select(AuditLog).where(AuditLog.action == "test.action.bg")).scalar_one().ip == ""


def test_http_audited_endpoint_records_ip(api):
    """端到端：中间件写入的 IP 必须落到审计行（同步路由跑在线程池里，上下文要能带过去）。"""
    init_db()
    headers = _admin_headers()
    resp = api.post("/api/admin/groups", headers=headers, json={"name": "研发部", "type": "部门", "parent_id": None})
    assert resp.status_code == 201, resp.text

    with db_session() as db:
        row = db.execute(select(AuditLog).where(AuditLog.action == "group.create")).scalar_one()
    assert row.ip, "HTTP 触发的审计必须带来源 IP（回归：ip 列恒为空）"


# ---------- A3 越权拒绝留痕 ----------
def test_authorization_denial_is_logged(caplog):
    init_db()
    with db_session() as db:
        dept = Group(name="研发部", type="部门", parent_id=None)
        db.add(dept)
        db.flush()
        actor = _mk_user(db, role="dept_admin", dept_group_id=dept.id)
        outsider = _mk_user(db, role="user")
        db.commit()
        actor_id, outsider_id, dept_id = actor.id, outsider.id, dept.id

    with db_session() as db, caplog.at_level(logging.WARNING, logger="quizhub"):
        with pytest.raises((DomainError, HTTPException)) as exc:
            # 目标用户不在部门管理员范围内 → 403，且必须留下 WARNING
            user_service.approve(db, actor_id, outsider_id, scope={dept_id})
        assert exc.value.status_code == 403

    messages = [rec.getMessage() for rec in caplog.records]
    assert any("越权拒绝" in m for m in messages), f"越权拒绝必须记日志，实际：{messages}"


# ---------- A4 删除分组清理悬空指派 ----------
def test_delete_group_strips_dangling_assignments():
    init_db()
    with db_session() as db:
        root = Group(name="集团", type="部门", parent_id=None)
        db.add(root)
        db.flush()
        leaf = Group(name="研发部", type="部门", parent_id=root.id)
        db.add(leaf)
        db.flush()
        admin = _mk_user(db)

        exam = ExamDefinition(
            name="考试",
            type="formal",
            rules={},
            group_ids=[root.id, leaf.id],
            status="draft",
            created_by=admin.id,
        )
        tpl = PaperTemplate(name="模板", mode="formal", config={}, group_ids=[leaf.id], created_by=admin.id)
        db.add(exam)
        db.add(tpl)
        db.commit()
        root_id, leaf_id, exam_id, tpl_id = root.id, leaf.id, exam.id, tpl.id

    with db_session() as db:
        group_service.delete_group(db, leaf_id)

    with db_session() as db:
        assert db.get(Group, leaf_id) is None
        assert db.get(ExamDefinition, exam_id).group_ids == [root_id], "只应剔除被删分组，其他指派保留"
        assert db.get(PaperTemplate, tpl_id).group_ids == []


# ---------- A5 练习作答体积上限 ----------
def test_practice_answer_size_is_bounded():
    """与 ExamAnswerIn 同口径：超大作答必须被拒，而不是原样落库。"""
    from app.core.limits import MAX_ANSWER_BYTES

    ok = PracticeAnswerIn(question_id=1, answer="x" * 100)
    assert ok.answer == "x" * 100

    with pytest.raises(ValidationError):
        PracticeAnswerIn(question_id=1, answer="x" * (MAX_ANSWER_BYTES + 1))


# ---------- C1 need_review ----------
def _seed_objective_exam(db, *, need_review: bool = False, duration_min: int = 30):
    student = _mk_user(db, role="user")
    bank = QuestionBank(name="库", practice_enabled=True)
    db.add(bank)
    db.flush()
    question = Question(bank_id=bank.id, type="单选题", question="1+1", options=["A", "B"], answer="A", score=2.0)
    db.add(question)
    db.flush()
    exam = ExamDefinition(
        name="考试",
        type="formal",
        manual_questions=[question.id],
        rules={},
        group_ids=None,
        duration_min=duration_min,
        pass_score=60,
        max_attempts=0,
        need_review=need_review,
        show_score_immediately=True,
        status="published",
        created_by=student.id,
    )
    db.add(exam)
    db.flush()
    return student.id, exam.id, question.id


def test_need_review_flag_is_honored_without_short_answers():
    """管理员显式要求复核时，即使组卷没有简答题也不能立即公布。"""
    init_db()
    with db_session() as db:
        student_id, exam_id, qid = _seed_objective_exam(db, need_review=True)
        db.commit()

    with db_session() as db:
        student = db.get(User, student_id)
        payload = exam_service.start_exam(db, student, exam_id)
        session_id = payload["session_id"]
        exam_service.submit_answer(db, student, session_id, qid, "A", 1)
        res = exam_service.submit_exam(db, student, session_id)
        assert res["need_review"] is True, "need_review 开关必须生效（原实现只写不读）"

    with db_session() as db:
        student = db.get(User, student_id)
        detail = exam_service.get_result(db, student, session_id)
    assert detail["published"] is False
    assert detail.get("need_review") is True


# ---------- C2 超时成绩原因 ----------
def test_overtime_result_reports_overtime_reason():
    init_db()
    with db_session() as db:
        student_id, exam_id, qid = _seed_objective_exam(db, duration_min=1)
        db.commit()

    with db_session() as db:
        student = db.get(User, student_id)
        payload = exam_service.start_exam(db, student, exam_id)
        session_id = payload["session_id"]
        exam_service.submit_answer(db, student, session_id, qid, "A", 1)
        # 交卷前把开始时间改到很久以前 → 服务端判定超时
        sess = db.get(ExamSession, session_id)
        sess.started_at = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        db.commit()
        res = exam_service.submit_exam(db, student, session_id)
        assert res["overtime"] is True

    with db_session() as db:
        student = db.get(User, student_id)
        detail = exam_service.get_result(db, student, session_id)

    assert detail["published"] is False
    assert detail.get("overtime") is True, "超时成绩必须给出「超时」原因，而不是「待复核」"
    assert "超时" in detail["message"]


# ---------- C4 畸形 xlsx ----------
def _zip_without_content_types() -> bytes:
    """合法 zip 但不是 xlsx（缺 [Content_Types].xml）——.docx/.zip 改名后的典型形态。"""
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<document/>")
    return buf.getvalue()


def test_malformed_zip_is_rejected_as_value_error():
    from app.utils import excel as excel_utils
    from app.utils import user_excel

    content = _zip_without_content_types()
    excel_utils.validate_workbook_archive(content)  # 能过 zip 校验（PK 魔数 + 解压上限）

    with pytest.raises(ValueError):
        excel_utils.parse_workbook(BytesIO(content))
    with pytest.raises(ValueError):
        user_excel.preview(content, 1)


# ---------- C5 单次解析 ----------
def test_parse_workbook_exposes_all_rows_and_caps_preview():
    from openpyxl import Workbook

    from app.utils.excel import HEADERS, parse_workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "单选题"
    ws.append(HEADERS["单选题"])
    for i in range(25):
        ws.append([f"题目{i}", "A.甲\nB.乙", "A", "", 2, "", 2, ""])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    parsed = parse_workbook(buf)
    assert parsed.all_rows, "完整解析结果必须可用（导入侧不再二次解析同一文件）"
    assert parsed.total == len(parsed.all_rows)
    assert len(parsed.rows) <= 20, "rows 仍是给前端的预览切片"
    assert [r.row_index for r in parsed.rows] == [r.row_index for r in parsed.all_rows[:20]]


# ---------- C6 判分边界 ----------
def test_zero_score_question_keeps_zero_score():
    """0 分是合法分值，不能被 `or` 当成缺失值补成 2 分。"""
    init_db()
    with db_session() as db:
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        db.add(Question(bank_id=bank.id, type="单选题", question="q", options=["A", "B"], answer="A", score=0.0))
        db.commit()

        paper = paper_service.generate_paper(db, {"type_quota": {"单选题": 1}, "max_questions": 10})

    assert paper["total_score"] == 0.0
    assert all(v == 0.0 for v in paper["scores"].values())


def test_is_passed_tolerates_float_boundary():
    assert grading.is_passed(60.0, 100.0, 60) is True
    assert grading.is_passed(59.0, 100.0, 60) is False
    # 浮点累加得到的 59.99999999999999 必须仍按压线处理
    assert grading.is_passed(59.99999999999999, 100.0, 60) is True
    assert grading.is_passed(0.0, 100.0, 0) is True
    assert grading.is_passed(0.0, 0.0, 0) is False, "空卷永远不及格"
    assert grading.is_passed(100.0, 100.0, 60, overtime=True) is False


def test_fullwidth_answers_are_normalized():
    """中文输入法下的全角字母/空格必须与半角等价。"""
    assert grading.grade("单选题", "A", "Ａ") is True
    assert grading.grade("填空题", [["HTTP"]], ["ＨＴＴＰ"]) is True
    assert grading.grade("填空题", [["A B"]], ["Ａ　Ｂ"]) is True
    assert grading.grade("多选题", "AB", "ＡＢ") is True


def test_single_choice_answer_shape_is_validated():
    """单选只接受单个字母；多选接受多个（选项集校验在导入路径另有实现）。"""
    init_db()
    from app.schemas.question import QuestionCreate

    def _create(answer: str) -> None:
        with db_session() as db:
            payload = QuestionCreate(type="单选题", question="q", options=["A", "B"], answer=answer)
            question_service.create_question(db, payload, scope=None)

    _create("A")  # 正常
    with pytest.raises((DomainError, HTTPException)):
        _create("AB")  # 单选多字母 → 该题永远无法作答
    with pytest.raises((DomainError, HTTPException)):
        _create("1")  # 非字母


# ---------- C7 统计日志 ----------
def test_stats_logs_unparseable_timestamps(caplog):
    init_db()
    from app.services import stats_service

    with db_session() as db, caplog.at_level(logging.WARNING, logger="quizhub"):
        stats_service.refresh_for_timestamps(db, ["not-a-timestamp", None])

    messages = [rec.getMessage() for rec in caplog.records]
    assert any("无法解析" in m for m in messages), f"坏时间戳必须留日志，实际：{messages}"


# ---------- N3 create_user 返回值 ----------
def test_create_user_returns_user():
    init_db()
    with db_session() as db:
        admin = _mk_user(db)
        db.commit()
        result = user_service.create_user(
            db, admin.id, f"{secrets.token_hex(4)}@example.com", "新人", "user", "pw123456"
        )
    assert isinstance(result, User), 'create_user 应直接返回 User（原实现返回 (User, "")）'


# ---------- N9 导入失败文案受控 ----------
def test_import_error_text_is_controlled():
    init_db()
    with db_session() as db:
        admin = _mk_user(db)
        db.commit()
        res = user_service.import_users(
            db,
            admin.id,
            [{"email": "bad-role@example.com", "role": "root", "password": "pw123456"}],
            scope=None,
            actor_role="super_admin",
        )

    assert res["failed"] == 1
    message = res["errors"][0]["error"]
    assert "角色非法" in message
    assert "INSERT" not in message and "UNIQUE" not in message and "constraint" not in message.lower()


# ---------- N10 响应契约 ----------
def test_question_and_bank_response_models_match_frontend_contract(api):
    init_db()
    headers = _admin_headers()

    bank = api.post("/api/admin/question-banks", headers=headers, json={"name": "库", "practice_enabled": True})
    assert bank.status_code == 201, bank.text
    bank_body = bank.json()
    assert {"id", "name", "group_id", "practice_enabled"} <= set(bank_body), bank_body

    created = api.post(
        "/api/admin/questions",
        headers=headers,
        json={"bank_id": bank_body["id"], "type": "单选题", "question": "q", "options": ["A", "B"], "answer": "A"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert {"id", "type", "question", "options", "answer", "score", "difficulty", "group_id"} <= set(body)
    assert "created_at" not in body, "响应 schema 应只暴露契约字段"

    updated = api.put(f"/api/admin/question-banks/{bank_body['id']}", headers=headers, json={"name": "新库"})
    assert updated.status_code == 200, updated.text
    updated_body = updated.json()
    assert updated_body["group_id"] is None, "原实现手拼 dict 漏了 group_id（与前端类型不符）"
    assert updated_body["practice_enabled"] is True
