"""管理端路由的权限守卫矩阵。

报告第五节的缺口：`/api/admin/*` 与 `/api/system/*` 只覆盖了部分守卫，
「普通用户打管理端路由」「dept_admin 打超管专属路由」缺少系统性断言 ——
把 `Depends(require_admin/require_super)` 从某个路由上删掉不会被任何用例发现。

矩阵对每条路由断言四件事：
1. 未登录 → 401；2. 普通用户 → 403；3. dept_admin：admin 路由不得 403、super 路由必须 403；
4. super_admin → 不得 401/403。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.database import SessionLocal, db_session, get_db, init_db
from app.main import create_app
from app.models.group import Group
from app.models.user import User

# require_admin：部门管理员可访问
ADMIN_ONLY: list[tuple[str, str, dict | None]] = [
    ("GET", "/api/admin/users", None),
    ("GET", "/api/admin/questions", None),
    ("GET", "/api/admin/question-banks", None),
    ("GET", "/api/admin/question-type-stats", None),
    ("GET", "/api/admin/exams", None),
    ("GET", "/api/admin/exam-results", None),
    ("GET", "/api/admin/review/pending", None),
    ("GET", "/api/admin/audit-logs", None),
    ("GET", "/api/admin/groups", None),
    ("GET", "/api/admin/panel/overview", None),
    ("POST", "/api/admin/exams/999999/archive", None),
    ("DELETE", "/api/admin/exams/999999", None),
]

# require_super：部门管理员必须被拒
SUPER_ONLY: list[tuple[str, str, dict | None]] = [
    ("GET", "/api/admin/exam-templates", None),
    ("GET", "/api/system/settings", None),
    ("GET", "/api/system/categories", None),
    ("POST", "/api/admin/panel/refresh", None),
    ("PUT", "/api/system/settings", {"category": "general", "updates": {"site_name": "站点"}}),
    ("POST", "/api/system/smtp/test", {"to_email": "someone@example.com"}),
]

ALL_ROUTES = [*ADMIN_ONLY, *SUPER_ONLY]


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


@pytest.fixture
def headers() -> dict[str, dict[str, str]]:
    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        out: dict[str, dict[str, str]] = {}
        for role, email in (
            ("user", "u@quizhub.com"),
            ("dept_admin", "d@quizhub.com"),
            ("super_admin", "s@quizhub.com"),
        ):
            user = User(
                email=email,
                password_hash="x",
                name=role,
                role=role,
                status="active",
                email_verified=True,
                dept_group_id=group.id if role == "dept_admin" else None,
            )
            db.add(user)
            db.flush()
            out[role] = {"Authorization": f"Bearer {create_access_token(user.id, {'ver': user.token_version})}"}
        db.commit()
        return out


@pytest.mark.parametrize(("method", "path", "body"), ALL_ROUTES)
def test_admin_routes_require_authentication(api, method, path, body):
    resp = api.request(method, path, json=body)
    assert resp.status_code == 401, f"{method} {path} → {resp.status_code}"


@pytest.mark.parametrize(("method", "path", "body"), ALL_ROUTES)
def test_admin_routes_reject_normal_user(api, headers, method, path, body):
    resp = api.request(method, path, json=body, headers=headers["user"])
    assert resp.status_code == 403, f"{method} {path} → {resp.status_code}"


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ONLY)
def test_dept_admin_allowed_on_admin_routes(api, headers, method, path, body):
    resp = api.request(method, path, json=body, headers=headers["dept_admin"])
    assert resp.status_code != 403, f"dept_admin 不应被 require_admin 拦住：{method} {path}"


@pytest.mark.parametrize(("method", "path", "body"), SUPER_ONLY)
def test_dept_admin_blocked_on_super_routes(api, headers, method, path, body):
    resp = api.request(method, path, json=body, headers=headers["dept_admin"])
    assert resp.status_code == 403, f"dept_admin 不应能访问超管路由：{method} {path}"


@pytest.mark.parametrize(("method", "path", "body"), ALL_ROUTES)
def test_super_admin_not_blocked(api, headers, method, path, body):
    resp = api.request(method, path, json=body, headers=headers["super_admin"])
    assert resp.status_code not in (401, 403), f"super_admin 被误拦：{method} {path} → {resp.status_code}"
