"""分层、上限与可观测性批次的回归测试。

1. 服务层/工具层不再依赖 FastAPI（`core/errors.py` 声明的分层约定，含结构性守卫）；
2. 组卷来源的标签筛选下推到 SQL（JSON1 元素匹配，不再全表载入 Python）；
3. 用户端可用考试列表有上限（避免整表载入）；
4. 审计日志时间边界：纯日期按业务时区解释、上界补到当天末尾，带偏移的按绝对时刻；
5. 简答复核不再污染 `objective_score`（该列语义是「客观题得分」）；
6. 5xx 有结构化日志（此前只有 uvicorn 裸堆栈）；
7. 上传时的 `bank_name` 有长度上限（与 QuestionBankCreate 同口径）。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.errors import DomainError
from app.core.security import create_access_token
from app.database import SessionLocal, db_session, get_db, init_db
from app.main import create_app
from app.models.exam import ExamDefinition
from app.models.group import Group, UserGroup
from app.models.question import Question, QuestionBank
from app.models.system import AuditLog
from app.models.user import User
from app.services import exam_service, question_service, user_service
from app.utils.excel import HEADERS


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


def _mk_user(db, email: str, role: str = "super_admin", dept_group_id: int | None = None) -> User:
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
    return {"Authorization": f"Bearer {create_access_token(user.id, {'ver': user.token_version})}"}


# ---------- 1. 分层：服务层不依赖 FastAPI ----------
def test_service_and_utils_layers_do_not_import_fastapi():
    """服务层/工具层不得 import fastapi：领域逻辑要能脱离传输框架复用与测试。"""
    root = Path(__file__).resolve().parent.parent / "app"
    offenders = [
        str(path.relative_to(root.parent))
        for path in [*root.joinpath("services").rglob("*.py"), *root.joinpath("utils").rglob("*.py")]
        if "import fastapi" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"服务层/工具层不应依赖 FastAPI：{offenders}"


# ---------- 2. 标签筛选下推 SQL ----------
def test_type_stats_filters_tags_in_sql():
    """命中任一标签即计数，且不做子串匹配（「网络」不应命中「网络基础」）。"""
    init_db()
    with db_session() as db:
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        for tags, qtype in (
            (["网络", "基础"], "单选题"),
            (["网络基础"], "单选题"),
            (None, "多选题"),
        ):
            db.add(
                Question(
                    bank_id=bank.id,
                    type=qtype,
                    question="q",
                    options=["A"],
                    answer="A",
                    analysis="",
                    difficulty=1,
                    score=2.0,
                    tags=tags,
                )
            )
        db.commit()

        assert question_service.type_stats(db, tags=["网络"])["单选题"] == 1
        assert question_service.type_stats(db, tags=["网络"])["多选题"] == 0
        assert question_service.type_stats(db, tags=["网络", "网络基础"])["单选题"] == 2
        # 无标签条件时仍走聚合（不因改造而漏题）
        assert question_service.type_stats(db)["单选题"] == 2


# ---------- 3. 可用考试列表上限 ----------
def test_list_available_is_capped():
    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        user = _mk_user(db, "capped@quizhub.com", "user", dept_group_id=group.id)
        for i in range(205):
            db.add(
                ExamDefinition(
                    name=f"考试{i}",
                    type="formal",
                    rules={},
                    group_ids=[group.id],
                    duration_min=60,
                    pass_score=60,
                    max_attempts=0,
                    show_score_immediately=True,
                    show_analysis=False,
                    need_review=False,
                    status="published",
                    created_by=user.id,
                )
            )
        db.commit()
        db.refresh(user)
        assert len(exam_service.list_available(db, user)) == 200


# ---------- 4. 审计时间边界 ----------
def _seed_audit_rows() -> None:
    with db_session() as db:
        for created_at in (
            "2026-02-12T20:00:00+00:00",  # 业务时区（+08）已是 2026-02-13 04:00
            "2026-02-13T02:00:00+00:00",  # 业务时区 2026-02-13 10:00
            "2026-02-13T18:00:00+00:00",  # 业务时区 2026-02-14 02:00
        ):
            db.add(AuditLog(actor=None, action="exam.create", target_type="exam", target_id="1", created_at=created_at))
        db.commit()


def test_audit_time_bounds_respect_business_timezone(api):
    init_db()
    with db_session() as db:
        admin = _mk_user(db, "audit@quizhub.com")
        db.commit()
        headers = _headers(admin)
    _seed_audit_rows()

    # 纯日期上界：按业务时区补到当天末尾（含 2026-02-13 本地全天）
    pure_date = api.get("/api/admin/audit-logs", headers=headers, params={"to": "2026-02-13"})
    assert pure_date.status_code == 200, pure_date.text
    assert pure_date.json()["total"] == 2

    # 显式 UTC 零点：就是绝对时刻，不得被静默扩成当天末尾
    explicit = api.get("/api/admin/audit-logs", headers=headers, params={"to": "2026-02-13T00:00:00+00:00"})
    assert explicit.json()["total"] == 1

    # 纯日期下界 + 上界：按业务时区取「本地一整天」（UTC 前一天 16:00 ~ 当天 15:59:59），
    # 不得丢掉本地凌晨的记录（旧实现把纯日期下界当 UTC 零点，会漏掉 2026-02-12T20:00Z 这条）
    business_day = api.get("/api/admin/audit-logs", headers=headers, params={"from": "2026-02-13", "to": "2026-02-13"})
    assert business_day.json()["total"] == 2


# ---------- 5. objective_score 不被复核污染 ----------
def test_review_does_not_pollute_objective_score():
    from app.models.record import ExamResult
    from app.services import review_service
    from tests.test_review_2026_09_23_fixes import _seed_review

    init_db()
    with db_session() as db:
        admin, _exam, result, reviews = _seed_review(db)
        result.objective_score = 40.0
        result.score = 40.0
        db.commit()
        review_service.review(db, reviews[0].id, "pass", None, admin, None)
        refreshed = db.get(ExamResult, result.id)
        assert refreshed is not None
        assert refreshed.score == 50.0, "复核得分应计入总分"
        assert refreshed.objective_score == 40.0, "客观题得分不应被简答复核改动"


# ---------- 6. 5xx 结构化日志 ----------
def test_unhandled_exception_is_logged(api, monkeypatch, caplog):
    from app.api import questions as questions_api

    init_db()
    with db_session() as db:
        admin = _mk_user(db, "boom@quizhub.com")
        db.commit()
        headers = _headers(admin)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(questions_api.question_service, "list_banks", _boom)

    client = TestClient(api.app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="quizhub"):
        resp = client.get("/api/admin/question-banks", headers=headers)

    assert resp.status_code == 500
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "未处理异常" in text
    assert "/api/admin/question-banks" in text


# ---------- 7. bank_name 长度上限 ----------
def test_upload_preview_rejects_overlong_bank_name(api):
    from io import BytesIO

    from openpyxl import Workbook

    init_db()
    with db_session() as db:
        admin = _mk_user(db, "upload@quizhub.com")
        db.commit()
        headers = _headers(admin)

    wb = Workbook()
    ws = wb.active
    ws.title = "单选题"
    ws.append(HEADERS["单选题"])
    ws.append(["题一", "A.甲\nB.乙", "A", "", 2, "", 2, ""])
    buf = BytesIO()
    wb.save(buf)

    files = {"file": ("q.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    resp = api.post("/api/admin/upload/preview", files=files, data={"bank_name": "库" * 101}, headers=headers)
    assert resp.status_code == 422, resp.text


# ---------- 8. 未配置 ENC_KEY 时保存加密设置 ----------
def test_settings_save_without_enc_key_returns_503(api):
    """未配置 TRAINING_ENC_KEY 时保存敏感设置给 503 + 可操作提示，而不是 500。"""
    init_db()
    with db_session() as db:
        admin = _mk_user(db, "enc@quizhub.com")
        db.commit()
        headers = _headers(admin)

    resp = api.put(
        "/api/system/settings",
        headers=headers,
        json={"category": "smtp", "updates": {"smtp_password": "secret"}},
    )
    assert resp.status_code == 503, resp.text
    assert "TRAINING_ENC_KEY" in resp.json()["detail"]


# ---------- 9. 用户导入的分组 id 预取分块 ----------
def test_import_users_prefetches_group_ids_in_chunks(monkeypatch):
    """预取分块后仍能识别全部合法分组（不会因分块丢数据）。

    单元格上限 1 万字符（约 2500 个 id/行）× 单次 5000 行，理论上能构造出数十万个候选 id，
    一条 `IN (...)` 会撞上 SQLite 的绑定参数上限（too many SQL variables → 整批 500）。
    """
    monkeypatch.setattr(user_service, "_GID_PREFETCH_CHUNK", 1)
    init_db()
    with db_session() as db:
        groups = [Group(name=f"g{i}", type="部门") for i in range(3)]
        db.add_all(groups)
        db.flush()
        admin = _mk_user(db, "chunk@quizhub.com")
        db.commit()
        group_ids = [g.id for g in groups]

        rows = [
            {
                "email": "chunked@quizhub.com",
                "name": "分块",
                "role": "user",
                "password": "pw123456",
                "status": "active",
                "group_ids": group_ids,
            }
        ]
        res = user_service.import_users(db, admin.id, rows, scope=None, actor_role="super_admin")
        assert res["success"] == 1

        user = db.execute(select(User).where(User.email == "chunked@quizhub.com")).scalar_one()
        assigned = {r[0] for r in db.execute(select(UserGroup.group_id).where(UserGroup.user_id == user.id)).all()}
        assert assigned == set(group_ids)


# ---------- 10. 服务层授权自证 ----------
def test_service_layer_rejects_admin_role_without_super_actor():
    """非超管调用方不得经服务层创建/设置管理员角色。

    路由已有一道校验，这里验证服务层的第二道防线：未来若有人写脚本或新路由直接调用
    `create_user`/`update_user`，也不会因为绕过路由而提权。
    """
    from app.models.user import ROLE_DEPT_ADMIN, ROLE_SUPER_ADMIN

    init_db()
    with db_session() as db:
        dept_admin = _mk_user(db, "dept-selfcheck@quizhub.com", "dept_admin")
        db.commit()

        with pytest.raises(DomainError) as create_exc:
            user_service.create_user(db, dept_admin.id, "boss@quizhub.com", "老板", ROLE_DEPT_ADMIN, "pw123456")
        assert create_exc.value.status_code == 403

        with pytest.raises(DomainError) as update_exc:
            user_service.update_user(db, dept_admin.id, dept_admin.id, None, ROLE_SUPER_ADMIN, None)
        assert update_exc.value.status_code == 403
