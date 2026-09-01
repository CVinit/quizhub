"""HTTP 层鉴权和越权回归测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.database import SessionLocal
from app.main import create_app
from app.models.group import Group
from app.models.user import User


def _client() -> TestClient:
    app = create_app()

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    from app.database import get_db

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def test_me_requires_authentication():
    with _client() as client:
        response = client.get("/api/auth/me")

    assert response.status_code == 401


def test_me_accepts_current_user_token():
    with SessionLocal() as db:
        user = User(
            email="http-user@example.com",
            password_hash="x",
            name="HTTP 用户",
            role="user",
            status="active",
            email_verified=True,
        )
        db.add(user)
        db.commit()
        token = create_access_token(user.id, {"ver": user.token_version})

    with _client() as client:
        response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["id"] == user.id


def test_http_department_admin_cannot_create_out_of_scope_question():
    with SessionLocal() as db:
        own = Group(name="HTTP 本部门", type="部门")
        other = Group(name="HTTP 其他部门", type="部门")
        db.add_all([own, other])
        db.flush()
        admin = User(
            email="http-admin@example.com",
            password_hash="x",
            name="管理员",
            role="dept_admin",
            status="active",
            email_verified=True,
            dept_group_id=own.id,
        )
        db.add(admin)
        db.commit()
        token = create_access_token(admin.id, {"ver": admin.token_version})

    with _client() as client:
        response = client.post(
            "/api/admin/questions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "type": "单选题",
                "question": "越权题目",
                "options": ["A", "B"],
                "answer": "A",
                "group_id": other.id,
            },
        )

    assert response.status_code == 403
