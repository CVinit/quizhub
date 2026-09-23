"""用户端 HTTP 路由与考试端到端流程测试。

覆盖 `api/exams.py`（57%）、`api/panel.py`、`api/records.py`、`api/audit.py`。
含两条端到端链路：
- 模拟考试：可选题库 → 预览组卷 → 开考 → 作答 → 交卷 → 成绩；
- 正式考试 + 简答复核：建卷 → 发布 → 作答 → 提交 → 管理端复核(fail) → 公布成绩。
  其中复核 fail 走 HTTP 层，是 E1（NULL 分数 → 500）的端到端回归。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.database import SessionLocal, get_db, init_db
from app.main import create_app
from app.models.question import Question, QuestionBank
from app.models.user import User


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


def _mk_user(db, email: str, role: str = "user") -> User:
    user = User(
        email=email,
        password_hash="x",
        name=email.split("@")[0],
        role=role,
        status="active",
        email_verified=True,
    )
    db.add(user)
    db.flush()
    return user


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, {'role': user.role, 'ver': user.token_version})}"}


def _mk_bank_with_questions(db, *, simple: int = 2, short: int = 0) -> QuestionBank:
    bank = QuestionBank(name="开放库", group_id=None, practice_enabled=True)
    db.add(bank)
    db.flush()
    for i in range(simple):
        db.add(
            Question(
                bank_id=bank.id,
                type="单选题",
                question=f"选择题{i}",
                options=["甲", "乙"],
                answer="A",
                analysis="解析",
                difficulty=1,
                tags=[],
                score=2,
            )
        )
    for i in range(short):
        db.add(
            Question(
                bank_id=bank.id,
                type="简答题",
                question=f"简答题{i}",
                options=None,
                answer=f"参考答案{i}",
                analysis="",
                difficulty=1,
                tags=[],
                score=5,
            )
        )
    db.flush()
    return bank


def _seed_student_and_admin() -> tuple[User, dict[str, str], dict[str, str]]:
    with SessionLocal() as db:
        admin = _mk_user(db, "admin@example.com", "super_admin")
        student = _mk_user(db, "student@example.com", "user")
        db.commit()
        return student, _headers(student), _headers(admin)


# ---------- 练习 ----------
def test_practice_endpoints_flow(api):
    init_db()
    with SessionLocal() as db:
        _mk_bank_with_questions(db, simple=1, short=1)
        student = _mk_user(db, "p@example.com")
        all_questions = db.execute(select(Question)).scalars().all()
        short_q = next(q for q in all_questions if q.type == "简答题")
        simple_q = next(q for q in all_questions if q.type == "单选题")
        db.commit()
        headers = _headers(student)

    modes = api.get("/api/records/practice/modes", headers=headers)
    assert modes.status_code == 200
    assert modes.json()["total"] == 2

    started = api.post("/api/records/practice/start", headers=headers, json={"mode": "sequence", "limit": 10})
    assert started.status_code == 200
    assert len(started.json()) == 2

    answered = api.post(
        "/api/records/practice/answer",
        headers=headers,
        json={"question_id": simple_q.id, "answer": "A", "mode": "sequence"},
    )
    assert answered.status_code == 200
    assert answered.json()["is_correct"] is True
    assert answered.json()["correct_answer"] == "A"

    short_answered = api.post(
        "/api/records/practice/answer",
        headers=headers,
        json={"question_id": short_q.id, "answer": "我的作答", "mode": "sequence"},
    )
    assert short_answered.status_code == 200
    assert short_answered.json()["is_correct"] is None
    assert short_answered.json()["reference_answer"] == short_q.answer

    # 简答未自评前 status 仍为 unanswered，因此进度只计客观题那 1 题
    assert api.get("/api/records/practice/progress", headers=headers).json()["practiced"] == 1
    recent = api.get("/api/records/practice/recent", headers=headers)
    assert recent.status_code == 200
    assert len(recent.json()) == 2

    assert (
        api.post(
            f"/api/records/questions/{simple_q.id}/toggle-mark",
            headers=headers,
            json={"marked": True, "note": "重点"},
        ).status_code
        == 200
    )
    assert (
        api.post(
            f"/api/records/questions/{short_q.id}/short-eval", headers=headers, json={"mastered": True}
        ).status_code
        == 200
    )
    # 自评后简答才计入进度
    assert api.get("/api/records/practice/progress", headers=headers).json()["practiced"] == 2
    # 已自评过 → 拒绝重复自评
    assert (
        api.post(
            f"/api/records/questions/{short_q.id}/short-eval", headers=headers, json={"mastered": False}
        ).status_code
        == 400
    )

    assert api.get("/api/panel/me", headers=headers).status_code == 200
    # 排行榜已按产品决策整体下线（2026-09-23）：端点不再存在
    assert api.get("/api/rank", headers=headers).status_code == 404

    # 未登录访问受保护路由
    assert api.get("/api/panel/me").status_code == 401


def test_practice_rejects_payload_violations(api):
    init_db()
    _student, headers, _admin = _seed_student_and_admin()

    bad_mode = api.post("/api/records/practice/start", headers=headers, json={"mode": "nope"})
    assert bad_mode.status_code == 422

    too_many = api.post("/api/records/practice/start", headers=headers, json={"mode": "sequence", "limit": 99999})
    assert too_many.status_code == 422

    note_too_long = api.post(
        "/api/records/questions/1/toggle-mark", headers=headers, json={"marked": True, "note": "x" * 1001}
    )
    assert note_too_long.status_code == 422


# ---------- 草稿 ----------
def test_draft_endpoints_and_size_limit(api):
    init_db()
    _student, headers, _admin = _seed_student_and_admin()

    assert api.get("/api/drafts/exam-setup", headers=headers).json()["exists"] is False

    saved = api.put("/api/drafts/exam-setup", headers=headers, json={"bank_ids": [1, 2]})
    assert saved.status_code == 200
    loaded = api.get("/api/drafts/exam-setup", headers=headers)
    assert loaded.json()["payload"] == {"bank_ids": [1, 2]}

    oversized = api.put("/api/drafts/exam-setup", headers=headers, json={"blob": "x" * (70 * 1024)})
    assert oversized.status_code == 413

    too_long_key = api.put(f"/api/drafts/{'k' * 100}", headers=headers, json={"a": 1})
    assert too_long_key.status_code == 422

    assert api.delete("/api/drafts/exam-setup", headers=headers).status_code == 200
    assert api.get("/api/drafts/exam-setup", headers=headers).json()["exists"] is False


# ---------- 模拟考试端到端 ----------
def test_mock_exam_end_to_end(api):
    init_db()
    with SessionLocal() as db:
        _mk_bank_with_questions(db, simple=3)
        student = _mk_user(db, "mock@example.com")
        db.commit()
        headers = _headers(student)

    banks = api.get("/api/exams/mock/banks", headers=headers)
    assert banks.status_code == 200
    assert banks.json()["total_questions"] == 3

    # 未传 size 时预览与开考都按默认题量（服务层统一收敛）
    preview = api.post("/api/exams/mock/preview", headers=headers, json={})
    assert preview.status_code == 200
    assert preview.json()["count"] == 3
    assert preview.json()["size_downgraded"] is True  # 3 < 默认 30

    manual = api.post(
        "/api/exams/mock/preview",
        headers=headers,
        json={"size": 2, "type_quota": {"单选题": 2}, "allocation": "manual", "objective_only": True},
    )
    assert manual.status_code == 200
    assert manual.json()["count"] == 2

    started = api.post("/api/exams/mock/start", headers=headers, json={"size": 3})
    assert started.status_code == 200
    session_id = started.json()["session_id"]
    questions = started.json()["questions"]
    assert len(questions) == 3
    assert "answer" not in questions[0]  # 考试中不回传答案

    version = started.json()["version"]
    answer = api.post(
        f"/api/exams/session/{session_id}/answer",
        headers=headers,
        json={"question_id": questions[0]["id"], "answer": "A", "version": version},
    )
    assert answer.status_code == 200
    new_version = answer.json()["version"]

    # 旧 version 重放 → 409
    replay = api.post(
        f"/api/exams/session/{session_id}/answer",
        headers=headers,
        json={"question_id": questions[0]["id"], "answer": "B", "version": version},
    )
    assert replay.status_code == 409

    detail = api.get(f"/api/exams/session/{session_id}/detail", headers=headers)
    assert detail.status_code == 200
    assert len(detail.json()["questions"]) == 3

    submitted = api.post(f"/api/exams/session/{session_id}/submit", headers=headers)
    assert submitted.status_code == 200
    assert submitted.json()["total_count"] == 3
    assert new_version >= 2

    result = api.get(f"/api/exams/session/{session_id}/result", headers=headers)
    assert result.status_code == 200
    assert result.json()["published"] is True

    # 服务端只认服务端时间：交卷后不能再作答
    after = api.post(
        f"/api/exams/session/{session_id}/answer",
        headers=headers,
        json={"question_id": questions[1]["id"], "answer": "A", "version": new_version},
    )
    assert after.status_code == 400


def test_mock_exam_start_is_idempotent_for_same_settings(api):
    init_db()
    with SessionLocal() as db:
        _mk_bank_with_questions(db, simple=2)
        student = _mk_user(db, "mock2@example.com")
        db.commit()
        headers = _headers(student)

    first = api.post("/api/exams/mock/start", headers=headers, json={"size": 2})
    second = api.post("/api/exams/mock/start", headers=headers, json={"size": 2})
    assert first.json()["session_id"] == second.json()["session_id"]


# ---------- 正式考试 + 简答复核端到端 ----------
def test_formal_exam_and_review_end_to_end(api):
    init_db()
    with SessionLocal() as db:
        _mk_bank_with_questions(db, simple=1, short=1)
        admin = _mk_user(db, "admin2@example.com", "super_admin")
        student = _mk_user(db, "formal@example.com")
        db.commit()
        question_ids = [q.id for q in db.execute(select(Question).order_by(Question.id)).scalars().all()]
        admin_headers = _headers(admin)
        student_headers = _headers(student)

    created = api.post(
        "/api/admin/exams",
        headers=admin_headers,
        json={
            "name": "正式考试",
            "type": "formal",
            "manual_questions": question_ids,
            "duration_min": 60,
            "pass_score": 1,
            "need_review": True,
            "group_ids": None,
        },
    )
    assert created.status_code == 201
    exam_id = created.json()["id"]

    assert api.get("/api/admin/exams", headers=admin_headers).status_code == 200
    assert api.post(f"/api/admin/exams/{exam_id}/publish", headers=admin_headers).status_code == 200

    available = api.get("/api/exams/available", headers=student_headers).json()
    assert any(e["id"] == exam_id for e in available)

    started = api.post(f"/api/exams/{exam_id}/start", headers=student_headers)
    assert started.status_code == 200
    session_id = started.json()["session_id"]
    questions = started.json()["questions"]

    # 客观题答对 + 简答作答
    version = started.json()["version"]
    for q in questions:
        payload = {"question_id": q["id"], "answer": "A" if q["type"] == "单选题" else "我的简答", "version": version}
        response = api.post(f"/api/exams/session/{session_id}/answer", headers=student_headers, json=payload)
        assert response.status_code == 200
        version = response.json()["version"]

    submitted = api.post(f"/api/exams/session/{session_id}/submit", headers=student_headers)
    assert submitted.status_code == 200
    assert submitted.json()["need_review"] is True

    # 简答未复核前成绩不公布
    assert api.get(f"/api/exams/session/{session_id}/result", headers=student_headers).json()["published"] is False

    pending = api.get("/api/admin/review/pending", headers=admin_headers)
    assert pending.status_code == 200
    reviews = pending.json()
    assert len(reviews) == 1
    review_id = reviews[0]["id"]

    # E1 端到端回归：判「不通过」必须成功（原实现写 NULL 分数 → 500）
    failed_review = api.post(f"/api/admin/review/{review_id}", headers=admin_headers, json={"verdict": "fail"})
    assert failed_review.status_code == 200
    assert failed_review.json()["verdict"] == "fail"

    published = api.post(f"/api/admin/exams/{exam_id}/publish-results", headers=admin_headers)
    assert published.status_code == 200
    assert published.json()["published"] == 1

    results = api.get("/api/admin/exam-results", headers=admin_headers, params={"exam_id": exam_id})
    assert results.status_code == 200
    assert len(results.json()) == 1
    assert results.json()[0]["score"] is not None

    # 归档 / 取消归档 / 有作答记录时不可删除
    assert api.post(f"/api/admin/exams/{exam_id}/archive", headers=admin_headers).status_code == 200
    assert api.post(f"/api/admin/exams/{exam_id}/unarchive", headers=admin_headers).status_code == 200
    assert api.delete(f"/api/admin/exams/{exam_id}", headers=admin_headers).status_code == 409

    assert (
        api.put(
            f"/api/admin/exams/{exam_id}",
            headers=admin_headers,
            json={"name": "改名", "duration_min": 90},
        ).status_code
        == 200
    )
    assert api.get("/api/admin/panel/overview", headers=admin_headers).status_code == 200
    assert api.post("/api/admin/panel/refresh", headers=admin_headers, params={"days": 2}).status_code == 200
    assert api.get("/api/admin/audit-logs", headers=admin_headers).status_code == 200
    assert api.get("/api/admin/exam-templates", headers=admin_headers).status_code == 200
    assert (
        api.post(
            "/api/admin/exam-templates/preview-paper",
            headers=admin_headers,
            json={"type_quota": {"单选题": 1}, "max_questions": 10},
        ).status_code
        == 200
    )


def test_student_cannot_start_exam_out_of_assigned_group(api):
    init_db()
    with SessionLocal() as db:
        from app.models.group import Group

        _mk_bank_with_questions(db, simple=1)
        admin = _mk_user(db, "admin3@example.com", "super_admin")
        outsider = _mk_user(db, "outsider@example.com")
        group = Group(name="限定部门", type="部门")
        db.add(group)
        db.flush()
        question_id = db.execute(select(Question)).scalars().first().id
        db.commit()
        admin_headers = _headers(admin)
        outsider_headers = _headers(outsider)
        group_id = group.id

    created = api.post(
        "/api/admin/exams",
        headers=admin_headers,
        json={
            "name": "限定考试",
            "type": "formal",
            "manual_questions": [question_id],
            "group_ids": [group_id],
        },
    )
    exam_id = created.json()["id"]
    api.post(f"/api/admin/exams/{exam_id}/publish", headers=admin_headers)

    # 未指派该分组的学员：列表不可见、直接猜 id 开考也 403
    assert all(e["id"] != exam_id for e in api.get("/api/exams/available", headers=outsider_headers).json())
    assert api.post(f"/api/exams/{exam_id}/start", headers=outsider_headers).status_code == 403


def test_practice_and_exam_ownership_guards(api):
    """会话/成绩必须按 user_id 收敛：他人 session_id 一律 404。"""
    init_db()
    with SessionLocal() as db:
        _mk_bank_with_questions(db, simple=2)
        owner = _mk_user(db, "owner@example.com")
        other = _mk_user(db, "other@example.com")
        db.commit()
        owner_headers = _headers(owner)
        other_headers = _headers(other)

    started = api.post("/api/exams/mock/start", headers=owner_headers, json={"size": 2})
    session_id = started.json()["session_id"]

    assert api.get(f"/api/exams/session/{session_id}/detail", headers=other_headers).status_code == 404
    assert api.get(f"/api/exams/session/{session_id}/result", headers=other_headers).status_code == 404
    assert api.post(f"/api/exams/session/{session_id}/submit", headers=other_headers).status_code == 404
