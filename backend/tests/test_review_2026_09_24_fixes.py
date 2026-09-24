"""2026-09-24 全面审查整改的回归测试（第二轮）。

覆盖本轮修复的确定性缺陷：

1. 组卷配置（rules/config）只校验体积、不校验结构 → 畸形 `type_quota` 落库后，
   「管理端考试列表」与「用户端可用考试」在 `int()` 处整体 500，且无法从界面修复；
2. 手工建题/改题绕过「答案形状」不变式（空位数、选项范围、拖拽映射、改选项不重校答案）
   → 存下永远判错、无法作答的题（Excel 导入路径对同一份数据是报错的）；
3. `.env.example` 的 Fernet 密钥生成命令产出的密钥被 `Fernet()` 拒绝；
4. 非 multipart 请求体没有全局体积上限（内存耗尽面）；
5. 改密后当前会话也失效，接口却只说「其它会话已失效」；
6. 审计日志把用户姓名写入 detail（PII 口径不一致）；
7. 交卷崩溃残留（scoring 且无成绩）时二次交卷误报「考试已结束」；
8. 建号时任意 IntegrityError 都被归因为「该邮箱已存在」。

（难度配比 / SSRF / 设置掩码 / ensure_defaults 的补齐用例见 tests/test_review_2026_09_24_coverage.py。）
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.errors import DomainError
from app.core.security import create_access_token
from app.database import SessionLocal, db_session, get_db, init_db
from app.main import create_app
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.group import Group, UserGroup
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult, ExamSession
from app.models.user import User
from app.services import user_service


@pytest.fixture
def api(tmp_path, monkeypatch):
    """HTTP 客户端：依赖覆盖为测试库会话（与 test_api_admin_routes.py 同构）。"""
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


def _mk_user(db, email: str, role: str = "user", dept_group_id: int | None = None) -> User:
    user = User(
        email=email,
        password_hash="x",
        name=email.split("@")[0],
        role=role,
        status="active",
        email_verified=True,
        dept_group_id=dept_group_id,
    )
    db.add(user)
    db.flush()
    return user


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, {'role': user.role, 'ver': user.token_version})}"}


def _seed_admin_and_group() -> tuple[dict[str, str], int]:
    with SessionLocal() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        admin = _mk_user(db, "root@example.com", "super_admin")
        db.commit()
        return _headers(admin), group.id


# ---------- C1：组卷配置结构必须在入口被拒绝 ----------


@pytest.mark.parametrize(
    "rules",
    [
        {"type_quota": {"单选题": "abc"}},  # 字符串值 → int() ValueError
        {"type_quota": {"单选题": None}},  # null 值 → int() TypeError
        {"type_quota": [1, 2]},  # 非 dict → .values() AttributeError
        {"type_quota": {"未知题型": 3}},  # 未知题型名 → 配额恒为空
        {"type_quota": {"单选题": 10**9}},  # 超出 0~1000
        {"max_questions": "abc"},
        {"bank_ids": "x"},  # 来源字段必须是数组
        {"difficulty_dist": {"1": "abc"}},
        {"order_mode": "???"},
    ],
)
def test_create_exam_rejects_malformed_rules(api, rules):
    """畸形组卷配置必须在创建时 400，绝不落库（落库后列表会整体 500）。"""
    init_db()
    admin_headers, group_id = _seed_admin_and_group()

    resp = api.post(
        "/api/admin/exams",
        headers=admin_headers,
        json={"name": "坏规则考试", "type": "formal", "group_ids": [group_id], "rules": rules},
    )

    assert resp.status_code == 400, resp.text
    with SessionLocal() as db:
        assert db.query(ExamDefinition).count() == 0, "畸形 rules 不得落库"


def test_update_exam_rejects_malformed_rules_without_resetting_paper(api):
    """更新时同样拒绝，且**不得**触发 confirm_reset 的破坏性作废。"""
    init_db()
    admin_headers, group_id = _seed_admin_and_group()
    created = api.post(
        "/api/admin/exams",
        headers=admin_headers,
        json={
            "name": "正式考试",
            "type": "formal",
            "group_ids": [group_id],
            "rules": {"type_quota": {"单选题": 2}},
        },
    )
    assert created.status_code == 201
    exam_id = created.json()["id"]

    with SessionLocal() as db:
        question = Question(
            type="单选题", question="Q1", options=["甲", "乙"], answer="A", difficulty=2, score=2.0, group_id=group_id
        )
        db.add(question)
        db.flush()
        db.add(ExamQuestion(exam_definition_id=exam_id, question_id=question.id, seq=0, score=2.0))
        db.commit()

    resp = api.put(
        f"/api/admin/exams/{exam_id}",
        headers=admin_headers,
        json={"rules": {"type_quota": {"单选题": "abc"}}, "confirm_reset": True},
    )
    assert resp.status_code == 400, resp.text

    with SessionLocal() as db:
        exam = db.get(ExamDefinition, exam_id)
        assert exam.rules == {"type_quota": {"单选题": 2}}, "被拒的更新不得改写 rules"
        assert db.query(ExamQuestion).filter_by(exam_definition_id=exam_id).count() == 1, "不得作废已固化卷面"


def test_legacy_malformed_rules_do_not_break_exam_lists(api):
    """历史脏数据（修复前已落库的畸形 rules）不得让两个列表 500（展示路径防御式取值）。"""
    init_db()
    admin_headers, group_id = _seed_admin_and_group()
    with SessionLocal() as db:
        student = _mk_user(db, "stu@example.com")
        db.add(UserGroup(user_id=student.id, group_id=group_id))
        db.add(
            ExamDefinition(
                name="脏数据考试",
                type="formal",
                rules={"type_quota": {"单选题": "abc"}},
                group_ids=[group_id],
                status="published",
                duration_min=30,
                pass_score=60,
                max_attempts=0,
            )
        )
        db.commit()
        student_headers = _headers(student)

    admin_list = api.get("/api/admin/exams", headers=admin_headers)
    assert admin_list.status_code == 200, admin_list.text
    items = admin_list.json()
    assert [item["name"] for item in items] == ["脏数据考试"]
    assert items[0]["total_questions"] == 0, "无法识别的配额按 0 展示，而不是让整个列表失败"

    available = api.get("/api/exams/available", headers=student_headers)
    assert available.status_code == 200, available.text
    assert [item["name"] for item in available.json()] == ["脏数据考试"]


def test_valid_rules_still_accepted(api):
    """合法配置不受影响（含难度配比与来源筛选）。"""
    init_db()
    admin_headers, group_id = _seed_admin_and_group()
    resp = api.post(
        "/api/admin/exams",
        headers=admin_headers,
        json={
            "name": "正常考试",
            "type": "formal",
            "group_ids": [group_id],
            "rules": {
                "type_quota": {"单选题": 5, "多选题": 2},
                "difficulty_dist": {"1": 0.3, "2": 0.7},
                "bank_ids": [1],
                "order_mode": "grouped",
                "max_questions": 50,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    listed = api.get("/api/admin/exams", headers=admin_headers).json()
    assert listed[0]["total_questions"] == 7


# ---------- C2：手工建题/改题必须与 Excel 路径同口径 ----------


def _seed_bank(group_id: int) -> int:
    with SessionLocal() as db:
        bank = QuestionBank(name="题库", group_id=group_id)
        db.add(bank)
        db.commit()
        return bank.id


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (
            {"type": "填空题", "question": "甲____与乙____分别是什么？", "answer": [["甲答案"]]},
            "答案空位数与题干不一致 → 恒判错",
        ),
        (
            {"type": "单选题", "question": "端口是？", "options": [], "answer": "A"},
            "选项为空 → 无法作答",
        ),
        (
            {"type": "单选题", "question": "端口是？", "options": None, "answer": "A"},
            "选项缺失 → 无法作答",
        ),
        (
            {"type": "拖拽题", "question": "匹配", "answer": {"HTTP": "80"}, "left_items": [], "right_items": []},
            "拖拽映射与左右项不一致 → 渲染不出可作答的题",
        ),
        (
            {"type": "多选题", "question": "选？", "options": ["甲", "乙"], "answer": "AA"},
            "多选答案字母重复 → 排序比较永不相等",
        ),
    ],
)
def test_create_question_rejects_unanswerable_shapes(api, payload, reason):
    """与 Excel 导入路径同口径：这些形状都会存下永远判错/无法作答的题。"""
    init_db()
    admin_headers, group_id = _seed_admin_and_group()
    bank_id = _seed_bank(group_id)

    resp = api.post(
        "/api/admin/questions",
        headers=admin_headers,
        json={**payload, "bank_id": bank_id, "group_id": group_id, "difficulty": 2, "score": 2},
    )

    assert resp.status_code == 400, f"{reason}；实际 {resp.status_code} {resp.text}"
    with SessionLocal() as db:
        assert db.query(Question).count() == 0, "非法形状不得落库"


def test_update_question_revalidates_answer_when_options_shrink(api):
    """只改 options（不重传 answer）时，仍须按新选项集重校存量答案。"""
    init_db()
    admin_headers, group_id = _seed_admin_and_group()
    bank_id = _seed_bank(group_id)
    created = api.post(
        "/api/admin/questions",
        headers=admin_headers,
        json={
            "type": "单选题",
            "question": "端口是？",
            "options": ["甲", "乙", "丙"],
            "answer": "C",
            "bank_id": bank_id,
            "group_id": group_id,
        },
    )
    assert created.status_code == 201, created.text
    qid = created.json()["id"]

    resp = api.put(f"/api/admin/questions/{qid}", headers=admin_headers, json={"options": ["甲", "乙"]})
    assert resp.status_code == 400, f"答案 C 已超出新选项范围，必须拒绝；实际 {resp.status_code} {resp.text}"

    with SessionLocal() as db:
        q = db.get(Question, qid)
        assert q.options == ["甲", "乙", "丙"], "被拒的更新不得落库"


def test_update_question_revalidates_blank_count_when_stem_changes(api):
    """只改题干（空位数变化）时，仍须按新题干重校填空答案。"""
    init_db()
    admin_headers, group_id = _seed_admin_and_group()
    bank_id = _seed_bank(group_id)
    created = api.post(
        "/api/admin/questions",
        headers=admin_headers,
        json={
            "type": "填空题",
            "question": "甲____是什么？",
            "answer": [["a"]],
            "bank_id": bank_id,
            "group_id": group_id,
        },
    )
    assert created.status_code == 201, created.text
    qid = created.json()["id"]

    resp = api.put(
        f"/api/admin/questions/{qid}",
        headers=admin_headers,
        json={"question": "甲____与乙____分别是什么？"},
    )
    assert resp.status_code == 400, f"题干变 2 空而答案仍 1 空，必须拒绝；实际 {resp.status_code} {resp.text}"


def test_update_question_allows_consistent_blank_count(api):
    """同时提交「新题干 + 匹配的新答案」必须放行（不能把合法编辑也拦死）。"""
    init_db()
    admin_headers, group_id = _seed_admin_and_group()
    bank_id = _seed_bank(group_id)
    created = api.post(
        "/api/admin/questions",
        headers=admin_headers,
        json={
            "type": "填空题",
            "question": "甲____是什么？",
            "answer": [["a"]],
            "bank_id": bank_id,
            "group_id": group_id,
        },
    )
    qid = created.json()["id"]
    resp = api.put(
        f"/api/admin/questions/{qid}",
        headers=admin_headers,
        json={"question": "甲____与乙____分别是什么？", "answer": [["甲"], ["乙"]]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["answer"] == [["甲"], ["乙"]]


def test_create_question_still_accepts_excel_equivalent_shapes(api):
    """合法的手工建题不受影响（选项内答案、题干无下划线按 1 空处理）。"""
    init_db()
    admin_headers, group_id = _seed_admin_and_group()
    bank_id = _seed_bank(group_id)
    for payload in (
        {"type": "单选题", "question": "端口是？", "options": ["80", "443"], "answer": "A"},
        {"type": "多选题", "question": "选？", "options": ["甲", "乙"], "answer": "AB"},
        {"type": "判断题", "question": "HTTP 无状态？", "answer": "正确"},
        {"type": "填空题", "question": "写一个端口：____", "answer": [["80", "8080"]]},
        {"type": "填空题", "question": "写一个端口", "answer": [["80"]]},
        {"type": "简答题", "question": "简述", "answer": "参考答案"},
        {"type": "拖拽题", "question": "匹配", "answer": {"HTTP": "80"}, "left_items": ["HTTP"], "right_items": ["80"]},
    ):
        resp = api.post(
            "/api/admin/questions",
            headers=admin_headers,
            json={**payload, "bank_id": bank_id, "group_id": group_id},
        )
        assert resp.status_code == 201, f"{payload} -> {resp.status_code} {resp.text}"


# ---------- C3：Fernet 密钥的文档命令与错误提示 ----------

_ENV_EXAMPLE = Path(__file__).resolve().parent.parent.parent / ".env.example"


def test_env_example_enc_key_command_produces_valid_key():
    """`.env.example` 给出的生成命令必须真的产出 Fernet 接受的密钥。

    回归：原注释写的是 `secrets.token_urlsafe(32)`（43 字符无 padding），`Fernet()` 会抛
    ValueError → 按文档部署的实例永远保存不了 SMTP 密码。此用例把「文档即契约」钉住。
    """
    if not _ENV_EXAMPLE.exists():  # pragma: no cover - 仅在打包/裁剪检出时跳过
        pytest.skip("仓库根目录缺少 .env.example")
    lines = _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    enc_idx = next(i for i, line in enumerate(lines) if line.startswith("TRAINING_ENC_KEY="))
    block = "\n".join(lines[max(0, enc_idx - 3) : enc_idx])

    match = re.search(r'python3 -c "([^"]+)"', block)
    assert match, "TRAINING_ENC_KEY 的注释块必须给出可直接复制的生成命令"

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(match.group(1), {"__name__": "__main__"})  # noqa: S102  命令来自本仓库文档
    key = buf.getvalue().strip()
    Fernet(key.encode())  # 不抛异常即合法（32 字节 url-safe base64）


def test_documented_bad_key_shape_is_rejected_by_fernet():
    """反向钉住「不能用 token_urlsafe(32)」这一结论，避免有人再改回文档里的旧写法。"""
    with pytest.raises(ValueError):
        Fernet(secrets.token_urlsafe(32).encode())


def test_malformed_enc_key_write_returns_actionable_503(api, monkeypatch):
    """密钥非空但非法时，保存敏感设置必须回可操作提示（503 + 生成命令），而不是英文 400。"""
    init_db()
    admin_headers, _group_id = _seed_admin_and_group()
    # 按 `.env.example` 的旧写法生成：43 字符无 padding，Fernet() 拒绝
    monkeypatch.setattr("app.core.security.SETTINGS_ENC_KEY", secrets.token_urlsafe(32))

    resp = api.put(
        "/api/system/settings",
        headers=admin_headers,
        json={"category": "smtp", "updates": {"smtp_password": "s3cret"}},
    )

    assert resp.status_code == 503, resp.text
    detail = resp.json()["detail"]
    assert "TRAINING_ENC_KEY" in detail
    assert "urlsafe_b64encode" in detail, "提示里必须给出可直接执行的修复命令"
    assert "Fernet key must be" not in detail, "不得回显 cryptography 的英文内部错误"


def test_config_check_enc_key_logs_error(caplog):
    """启动期校验：非法密钥必须留下 ERROR（而不是等管理员保存 SMTP 密码才发现）。"""
    from app import config

    with caplog.at_level("ERROR", logger="quizhub"):
        config._check_enc_key(secrets.token_urlsafe(32))
        config._check_enc_key("not-base64!!")

    messages = [record.message for record in caplog.records]
    assert sum("TRAINING_ENC_KEY" in message for message in messages) == 2


def test_config_check_enc_key_accepts_valid_key(caplog):
    """合法密钥不得产生任何 ERROR 噪声。"""
    from app import config

    with caplog.at_level("ERROR", logger="quizhub"):
        config._check_enc_key(Fernet.generate_key().decode())

    assert not [record for record in caplog.records if record.levelname == "ERROR"]


# ---------- S1：非 multipart 请求体的解析前上限 ----------


def test_oversized_json_body_rejected_with_413(api):
    """声明了超大 Content-Length 的 JSON 请求必须在解析前被拒（未登录端点同样生效）。"""
    resp = api.post(
        "/api/auth/login",
        content=b"x" * (2 * 1024 * 1024),
        headers={"content-type": "application/json"},
    )

    assert resp.status_code == 413, resp.text
    assert "请求体不能超过" in resp.json()["detail"]


def test_body_limit_counts_stream_without_content_length():
    """声明缺失（chunked / 伪造小值）时，必须按实际收到的字节数拦截。"""
    from app.core.body_limit import BodySizeLimitMiddleware

    drained: list[str] = []

    async def downstream(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] == "http.request" and not message.get("more_body"):
                break
        drained.append("drained")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    chunks = [{"type": "http.request", "body": b"x" * 600_000, "more_body": True} for _ in range(3)]  # 1.8MB
    sent: list[dict] = []

    async def receive():
        return chunks.pop(0)

    async def send(message):
        sent.append(message)

    middleware = BodySizeLimitMiddleware(downstream, max_bytes=1024 * 1024)
    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/auth/login",
                "headers": [(b"content-type", b"application/json")],
            },
            receive,
            send,
        )
    )

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert not drained, "超限后不得继续把 body 交给下游应用"


def test_body_limit_ignores_multipart_uploads(api):
    """multipart 上传不走全局 JSON 阈值（其上限由 upload_max_size_mb + read_limited 决定）。"""
    init_db()
    admin_headers, _group_id = _seed_admin_and_group()
    payload = b"PK\x03\x04" + b"x" * (1200 * 1024)  # 1.2MB > 1MB，但是合法的 multipart 体积

    resp = api.post(
        "/api/admin/users/import/preview",
        headers=admin_headers,
        files={"file": ("users.xlsx", payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert resp.status_code != 413, "multipart 不应被 JSON 体积阈值拦截"
    assert resp.status_code == 400  # 内容不是有效 xlsx，由解析层给出 400


def test_blocked_svg_404_still_carries_security_headers(api):
    """提前返回的 404 也必须带安全响应头（原实现只在正常响应上设置）。"""
    resp = api.get("/files/evil.svg")

    assert resp.status_code == 404
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert resp.headers["Referrer-Policy"] == "same-origin"


# ---------- S2：改密必须回传可用的新 token ----------


def test_change_password_returns_usable_fresh_token(api):
    """改密后旧 token 立即失效，但响应里的新 token 必须能直接用（否则用户被莫名踢下线）。"""
    from app.core.security import hash_password

    init_db()
    with SessionLocal() as db:
        user = User(
            email="stu@example.com",
            password_hash=hash_password("oldpass1"),
            name="学员",
            role="user",
            status="active",
            email_verified=True,
        )
        db.add(user)
        db.commit()
        old_headers = _headers(user)

    resp = api.post(
        "/api/auth/change-password",
        headers=old_headers,
        json={"old_password": "oldpass1", "new_password": "newpass1"},
    )
    assert resp.status_code == 200, resp.text
    new_token = resp.json()["access_token"]

    assert api.get("/api/auth/me", headers=old_headers).status_code == 401, "旧 token 必须立即失效"
    me = api.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert me.status_code == 200, "新 token 必须可直接使用，用户不应被迫重新登录"
    assert me.json()["email"] == "stu@example.com"

    relogin = api.post("/api/auth/login", json={"username": "stu@example.com", "password": "newpass1"})
    assert relogin.status_code == 200, relogin.text


# ---------- S3：审计 detail 不得落 PII ----------


def test_audit_detail_does_not_store_user_name(api):
    """改姓名时审计只记字段名，不落姓名明文（audit-logs 对部门管理员开放、会回显 detail）。"""
    init_db()
    admin_headers, _group_id = _seed_admin_and_group()
    created = api.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"email": "pii@example.com", "name": "原姓名", "password": "pw123456"},
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]

    assert api.put(f"/api/admin/users/{user_id}", headers=admin_headers, json={"name": "新姓名"}).status_code == 200

    logs = api.get("/api/admin/audit-logs", headers=admin_headers, params={"action": "user.update"}).json()["items"]
    assert logs, "应当留下一条 user.update 审计"
    detail = json.dumps(logs[0]["detail"], ensure_ascii=False)
    assert "新姓名" not in detail, "姓名属 PII，不得写入审计 detail"
    assert "原姓名" not in detail
    assert "name" in detail, "仍须能看出「改了哪个字段」"


# ---------- S4：崩溃残留的 scoring 会话必须能续算 ----------


def _seed_stuck_session(*, minutes_ago: int) -> tuple[int, dict[str, str]]:
    """造一个「已置 scoring 但无成绩」的会话（模拟进程在两步之间被中断）。"""
    with SessionLocal() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        student = _mk_user(db, "stu@example.com")
        db.add(UserGroup(user_id=student.id, group_id=group.id))
        question = Question(
            type="单选题",
            question="HTTP 默认端口？",
            options=["80", "443"],
            answer="A",
            difficulty=2,
            score=2.0,
            group_id=group.id,
        )
        db.add(question)
        db.flush()
        exam = ExamDefinition(
            name="正式考试",
            type="formal",
            rules={"type_quota": {"单选题": 1}},
            group_ids=[group.id],
            status="published",
            duration_min=90,
            pass_score=60,
            max_attempts=0,
        )
        db.add(exam)
        db.flush()
        db.add(ExamQuestion(exam_definition_id=exam.id, question_id=question.id, seq=0, score=2.0))
        stale = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
        session = ExamSession(
            exam_definition_id=exam.id,
            user_id=student.id,
            status="scoring",
            answers={str(question.id): {"answer": "A"}},
            version=1,
            started_at=stale,
            submitted_at=stale,
        )
        db.add(session)
        db.commit()
        return session.id, _headers(student)


def test_submit_exam_recovers_stuck_scoring_session(api):
    """超过回收窗口的残留会话：二次交卷应重跑结算，而不是谎报「考试已结束」。"""
    init_db()
    session_id, headers = _seed_stuck_session(minutes_ago=31)

    resp = api.post(f"/api/exams/session/{session_id}/submit", headers=headers)

    assert resp.status_code == 200, f"崩溃残留必须能续算，实际 {resp.status_code} {resp.text}"
    assert resp.json()["score"] == 2.0
    with SessionLocal() as db:
        assert db.query(ExamResult).filter_by(exam_session_id=session_id).count() == 1, "应补出成绩记录"


def test_submit_exam_reports_scoring_in_progress_not_finished(api):
    """仍在结算窗口内的会话（并发提交中）：必须 409「正在结算中」，且不得重复建成绩。"""
    init_db()
    session_id, headers = _seed_stuck_session(minutes_ago=1)

    resp = api.post(f"/api/exams/session/{session_id}/submit", headers=headers)

    assert resp.status_code == 409, resp.text
    assert "结算" in resp.json()["detail"]
    with SessionLocal() as db:
        assert db.query(ExamResult).filter_by(exam_session_id=session_id).count() == 0


# ---------- S5：建号失败必须归因准确 ----------


def test_create_user_rejects_missing_dept_group():
    """服务层直调：不存在的 dept_group_id 必须 400，而不是靠外键失败后误报「该邮箱已存在」。"""
    init_db()
    with db_session() as db:
        actor = _mk_user(db, "root@example.com", "super_admin")
        db.commit()

        with pytest.raises(DomainError) as exc:
            user_service.create_user(
                db,
                actor.id,
                "newbie@example.com",
                password="pw123456",
                dept_group_id=999999,
                actor_role="super_admin",
            )

        assert exc.value.status_code == 400
        assert "分组" in exc.value.detail


# ---------- N3/N4：发布通知只发一次；列表接口的响应契约 ----------


class _RecordingBackground:
    """记录后台任务的替身（结构上满足 core.background.BackgroundTaskQueue 协议）。"""

    def __init__(self) -> None:
        self.tasks: list[tuple] = []

    def add_task(self, func, *args, **kwargs) -> None:
        self.tasks.append((func, args, kwargs))


def test_publish_exam_notifies_only_on_first_publish():
    """通知只在 draft→published 发一次；归档后重新发布不得再次群发。"""
    from app.services import exam_service

    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        student = _mk_user(db, "stu@example.com")
        db.add(UserGroup(user_id=student.id, group_id=group.id))
        question = Question(
            type="单选题",
            question="端口？",
            options=["80", "443"],
            answer="A",
            difficulty=2,
            score=2.0,
            group_id=group.id,
        )
        db.add(question)
        db.flush()
        exam = ExamDefinition(
            name="正式考试",
            type="formal",
            rules={"type_quota": {"单选题": 1}},
            group_ids=[group.id],
            status="draft",
            duration_min=30,
            pass_score=60,
            max_attempts=0,
        )
        db.add(exam)
        db.commit()
        exam_id = exam.id

    with db_session() as db:
        bg = _RecordingBackground()
        exam_service.publish_exam(db, exam_id, None, bg)
        assert bg.tasks, "首次发布应发出通知"

    with db_session() as db:
        bg = _RecordingBackground()
        exam_service.publish_exam(db, exam_id, None, bg)
        assert not bg.tasks, "重复点击发布不得再次群发"

    with db_session() as db:
        exam_service.archive_exam(db, exam_id, None)

    with db_session() as db:
        bg = _RecordingBackground()
        exam_service.publish_exam(db, exam_id, None, bg)
        assert not bg.tasks, "归档后重新发布同样不得再次群发（判据是「当前是 draft」）"


def test_list_response_models_keep_contract_fields(api):
    """用户/题库列表的响应模型不得抹掉前端依赖的字段（此前是无约束的手拼 dict）。"""
    init_db()
    admin_headers, group_id = _seed_admin_and_group()
    _seed_bank(group_id)
    created = api.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"email": "stu@example.com", "name": "学员", "password": "pw123456"},
    )
    assert created.status_code == 201, created.text

    users = api.get("/api/admin/users", headers=admin_headers).json()
    assert set(users) == {"total", "page", "page_size", "items"}
    assert {"id", "email", "name", "role", "status", "email_verified", "dept_group_id", "groups"} <= set(
        users["items"][0]
    )

    banks = api.get("/api/admin/question-banks", headers=admin_headers).json()
    assert {"id", "name", "group_id", "practice_enabled", "question_count"} <= set(banks[0])
