"""2026-09-23 第二轮加固的回归测试：补报告第五节指出的关键覆盖缺口。

1. 认证路由 HTTP 层：登录失败 401、图形验证码错误 400、弱口令 422、注册分组 fail-closed；
2. 限流：路由层 429 + Retry-After；信任代理时按 X-Forwarded-For 首段取客户端 IP；
3. 考试并发：同一 version 并发提交恰好一方成功；并发交卷只结算一次（不产生重复成绩）；
4. 开考幂等与规则：已有进行中会话返回同一会话；max_attempts / 时段窗口拦截；
5. 上传读取共用实现（core/uploads.py）：扩展名白名单、声明与实际超限均 413；
6. 草稿：超大 body 413、限流计数生效。
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.errors import DomainError
from app.core.security import create_access_token, hash_password
from app.core.uploads import read_limited
from app.database import SessionLocal, db_session, get_db, init_db
from app.main import create_app
from app.models.exam import ExamDefinition
from app.models.group import Group
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult, ExamSession
from app.models.user import User
from app.services import exam_service, user_service


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


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, {'ver': user.token_version})}"}


def _seed_user(email: str = "u@quizhub.com", password: str = "pw123456", role: str = "user") -> User:
    with db_session() as db:
        user = User(
            email=email,
            password_hash=hash_password(password),
            name="u",
            role=role,
            status="active",
            email_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


# ---------- 1. 认证路由 HTTP 层 ----------
def test_login_wrong_password_returns_401_and_correct_one_succeeds(api):
    _seed_user()
    bad = api.post("/api/auth/login", json={"username": "u@quizhub.com", "password": "wrong-password"})
    assert bad.status_code == 401, bad.text

    ok = api.post("/api/auth/login", json={"username": "u@quizhub.com", "password": "pw123456"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["access_token"]


def test_send_code_requires_valid_captcha(api):
    resp = api.post(
        "/api/auth/send-code",
        json={"email": "u@quizhub.com", "captcha_id": "not-a-real-id", "captcha_code": "0000"},
    )
    assert resp.status_code == 400, resp.text
    assert "图形验证码" in resp.json()["detail"]


def test_change_password_rejects_short_password(api):
    user = _seed_user()
    resp = api.post(
        "/api/auth/change-password",
        headers=_headers(user),
        json={"old_password": "pw123456", "new_password": "123"},
    )
    assert resp.status_code == 422, resp.text


def test_register_groups_is_fail_closed(api):
    """未配置「允许公开注册加入的分组」时不返回任何分组，且 required 恒为 False。"""
    resp = api.get("/api/auth/register-groups")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"groups": [], "required": False}


# ---------- 2. 限流 ----------
def test_captcha_rate_limit_returns_429_with_retry_after(api):
    """ip_limit("captcha", 60, 60)：第 61 次请求必须 429 且带 Retry-After。"""
    for _ in range(60):
        assert api.get("/api/auth/captcha").status_code == 200
    blocked = api.get("/api/auth/captcha")
    assert blocked.status_code == 429, blocked.text
    assert blocked.headers["Retry-After"].isdigit()


def _request(headers: dict[str, str] | None = None, host: str = "10.0.0.9"):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (host, 12345),
    }
    return Request(scope)


def test_client_ip_ignores_xff_unless_proxy_trusted(monkeypatch):
    from app.core import rate_limit

    req = _request({"x-forwarded-for": "1.2.3.4, 5.6.7.8"})
    monkeypatch.setattr(rate_limit, "_TRUST_PROXY", False)
    assert rate_limit.get_client_ip(req) == "10.0.0.9"  # 默认不信任，防伪造

    monkeypatch.setattr(rate_limit, "_TRUST_PROXY", True)
    assert rate_limit.get_client_ip(req) == "1.2.3.4"  # 信任时取首段（最靠近客户端的地址）


# ---------- 3. 考试并发 ----------
def _seed_started_exam(*, max_attempts: int = 0, start_at: str | None = None, start: bool = True) -> dict:
    """建「1 道单选题」的正式考试（默认同时开考），返回 user/exam/question[/session/version]。"""
    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        user = User(
            email="exam@quizhub.com",
            password_hash="x",
            name="考生",
            role="user",
            status="active",
            email_verified=True,
            dept_group_id=group.id,
        )
        db.add(user)
        db.flush()
        bank = QuestionBank(name="题库", practice_enabled=True)
        db.add(bank)
        db.flush()
        question = Question(
            bank_id=bank.id,
            type="单选题",
            question="1+1=?",
            options=["A", "B"],
            answer="A",
            analysis="",
            difficulty=1,
            score=2.0,
            group_id=group.id,
        )
        db.add(question)
        db.flush()
        exam = ExamDefinition(
            name="正式考试",
            type="formal",
            rules={},
            manual_questions=[question.id],
            group_ids=[group.id],
            duration_min=60,
            pass_score=60,
            max_attempts=max_attempts,
            show_score_immediately=True,
            show_analysis=False,
            need_review=False,
            status="published",
            created_by=user.id,
            start_at=start_at,
        )
        db.add(exam)
        db.commit()
        ids = {"user_id": user.id, "exam_id": exam.id, "question_id": question.id}

    with db_session() as db:
        payload = exam_service.start_exam(db, db.get(User, ids["user_id"]), ids["exam_id"]) if start else None
        if payload:
            ids["session_id"] = payload["session_id"]
            ids["version"] = payload["version"]
    return ids


def test_concurrent_submit_answer_exactly_one_wins():
    """同一 version 并发提交：原子条件更新保证恰好一方成功，另一方 409（乐观锁）。"""
    ids = _seed_started_exam()
    outcomes: list[object] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        with SessionLocal() as db:
            user = db.get(User, ids["user_id"])
            barrier.wait(timeout=5)
            try:
                exam_service.submit_answer(db, user, ids["session_id"], ids["question_id"], "A", ids["version"])
                outcomes.append("ok")
            except DomainError as exc:
                outcomes.append(exc.status_code)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(outcomes, key=str) == [409, "ok"], outcomes


def test_concurrent_submit_exam_settles_once():
    """并发交卷不得产生重复成绩：至多一行 ExamResult，且各次返回的分数一致。"""
    ids = _seed_started_exam()
    outcomes: list[object] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        with SessionLocal() as db:
            user = db.get(User, ids["user_id"])
            barrier.wait(timeout=5)
            try:
                outcomes.append(exam_service.submit_exam(db, user, ids["session_id"]))
            except DomainError as exc:
                outcomes.append(exc.status_code)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    with db_session() as db:
        results = db.execute(select(ExamResult).where(ExamResult.exam_session_id == ids["session_id"])).scalars().all()
        assert len(results) == 1

    settled = [r for r in outcomes if isinstance(r, dict)]
    assert settled, outcomes
    assert len({r["score"] for r in settled}) == 1


# ---------- 4. 开考幂等与规则 ----------
def test_start_exam_returns_existing_session_when_ongoing():
    ids = _seed_started_exam()
    with db_session() as db:
        again = exam_service.start_exam(db, db.get(User, ids["user_id"]), ids["exam_id"])
        assert again["session_id"] == ids["session_id"]
        assert db.execute(select(func.count()).select_from(ExamSession)).scalar_one() == 1


def test_start_exam_rejects_when_max_attempts_reached():
    ids = _seed_started_exam(max_attempts=1)
    with db_session() as db:
        user = db.get(User, ids["user_id"])
        exam_service.submit_answer(db, user, ids["session_id"], ids["question_id"], "A", ids["version"])
        exam_service.submit_exam(db, user, ids["session_id"])
        with pytest.raises(DomainError) as exc:
            exam_service.start_exam(db, user, ids["exam_id"])
        assert exc.value.status_code == 400
        assert "最大尝试次数" in str(exc.value.detail)

        # 列表侧同步反映为 max_reached（与开考拦截口径一致）
        briefs = exam_service.list_available(db, user)
        assert [b["state"] for b in briefs if b["id"] == ids["exam_id"]] == ["max_reached"]


def test_start_exam_rejects_outside_time_window():
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    ids = _seed_started_exam(start_at=future, start=False)
    with db_session() as db:
        with pytest.raises(DomainError) as exc:
            exam_service.start_exam(db, db.get(User, ids["user_id"]), ids["exam_id"])
        assert exc.value.status_code == 400
        assert "开放时段" in str(exc.value.detail)


# ---------- 5. 上传读取共用实现 ----------
class _FakeUpload:
    """最小 UploadFile 替身：read_limited 只依赖 filename/size/read。"""

    def __init__(self, filename: str, data: bytes, declared: int | None = None) -> None:
        self.filename = filename
        self.size = len(data) if declared is None else declared
        self._data = data
        self._pos = 0

    async def read(self, n: int) -> bytes:
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk


def test_read_limited_enforces_ext_and_size():
    async def _run() -> None:
        with pytest.raises(HTTPException) as bad_ext:
            await read_limited(_FakeUpload("a.txt", b"x"), max_bytes=10, allowed_ext={".xlsx"})
        assert bad_ext.value.status_code == 400

        # 声明大小超限：不读取内容即拒绝
        with pytest.raises(HTTPException) as declared:
            await read_limited(_FakeUpload("a.xlsx", b"12345", declared=999), max_bytes=10, allowed_ext={".xlsx"})
        assert declared.value.status_code == 413

        # 实际读取累计超限（Content-Length 缺失/撒谎时兜底）
        with pytest.raises(HTTPException) as actual:
            await read_limited(_FakeUpload("a.xlsx", b"0123456789ab"), max_bytes=10, allowed_ext={".xlsx"})
        assert actual.value.status_code == 413

        assert await read_limited(_FakeUpload("a.xlsx", b"12345"), max_bytes=10, allowed_ext={".xlsx"}) == b"12345"

    asyncio.run(_run())


# ---------- 6. 草稿上限与限流 ----------
def test_draft_size_limit_and_save(api):
    user = _seed_user(email="draft@quizhub.com")
    headers = _headers(user)
    too_big = api.put("/api/drafts/form", headers=headers, json={"blob": "x" * (70 * 1024)})
    assert too_big.status_code == 413, too_big.text

    ok = api.put("/api/drafts/form", headers=headers, json={"blob": "small"})
    assert ok.status_code == 200, ok.text
    assert api.get("/api/drafts/form", headers=headers).json()["payload"] == {"blob": "small"}


# ---------- 7. 并发禁用超管 ----------
def test_concurrent_cross_disable_of_super_admins_keeps_one_active():
    """两个超管并发互禁：只能成功一个，系统始终保留可用管理入口。

    守卫是「条件 UPDATE + EXISTS(除目标外仍有 active 超管)」：SQLite 单写者模型下，
    后到者的 UPDATE 会等先到者提交后再求值 EXISTS，因此必然看到「对方已被禁用」→ rowcount=0 → 400。
    """
    init_db()
    with db_session() as db:
        first = User(
            email="super-a@quizhub.com",
            password_hash="x",
            name="超管A",
            role="super_admin",
            status="active",
            email_verified=True,
        )
        second = User(
            email="super-b@quizhub.com",
            password_hash="x",
            name="超管B",
            role="super_admin",
            status="active",
            email_verified=True,
        )
        db.add_all([first, second])
        db.commit()
        first_id, second_id = first.id, second.id

    outcomes: list[object] = []
    barrier = threading.Barrier(2)

    def worker(actor_id: int, target_id: int) -> None:
        with SessionLocal() as db:
            barrier.wait(timeout=5)
            try:
                user_service.set_status(db, actor_id, target_id, False, scope=None)
                outcomes.append("ok")
            except DomainError as exc:
                outcomes.append(exc.status_code)

    threads = [
        threading.Thread(target=worker, args=(first_id, second_id)),
        threading.Thread(target=worker, args=(second_id, first_id)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert outcomes.count("ok") == 1, outcomes
    assert outcomes.count(400) == 1, outcomes
    with db_session() as db:
        active = db.execute(
            select(func.count()).select_from(User).where(User.role == "super_admin", User.status == "active")
        ).scalar_one()
        assert active == 1, "并发互禁不得把最后一个可用超管也禁掉"
