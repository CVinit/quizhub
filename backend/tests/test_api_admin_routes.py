"""管理端 HTTP 路由测试（users / questions / groups / system）。

覆盖率报告显示这些路由模块（`api/users.py` 43%、`api/questions.py` 41%、
`api/groups.py` 47%、`api/system.py` 46%）此前仅在服务层被间接覆盖，
HTTP 契约、依赖注入与权限守卫（require_admin/require_super）几乎没有验证。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.database import SessionLocal, get_db, init_db
from app.main import create_app
from app.models.group import Group
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


def _mk_user(db, email: str, role: str = "super_admin", *, dept_group_id: int | None = None) -> User:
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


def _seed_admin() -> tuple[int, dict[str, str]]:
    with SessionLocal() as db:
        admin = _mk_user(db, "root@example.com", "super_admin")
        db.commit()
        return admin.id, _headers(admin)


def _seed_two_depts() -> tuple[dict[str, str], int, int, int]:
    """集团→{研发部, 市场部}，dept_admin 归属研发部（研发部下还有一个子分组）。

    返回 (dept_admin 请求头, 研发部 id, 市场部 id, 研发一组 id)。
    """
    with SessionLocal() as db:
        root = Group(name="集团", type="部门")
        db.add(root)
        db.flush()
        rd = Group(name="研发部", type="部门", parent_id=root.id)
        mk = Group(name="市场部", type="部门", parent_id=root.id)
        db.add_all([rd, mk])
        db.flush()
        team = Group(name="研发一组", type="部门", parent_id=rd.id)
        db.add(team)
        db.flush()
        admin = _mk_user(db, "dept-scope@example.com", "dept_admin", dept_group_id=rd.id)
        db.commit()
        return _headers(admin), rd.id, mk.id, team.id


# ---------- 用户管理 ----------
def test_admin_user_crud_flow(api):
    init_db()
    _admin_id, admin_headers = _seed_admin()

    created = api.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"email": "stu@example.com", "name": "学员", "role": "user", "password": "pw123456"},
    )
    assert created.status_code == 201
    user_id = created.json()["id"]

    listed = api.get("/api/admin/users", headers=admin_headers, params={"keyword": "stu"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    updated = api.put(f"/api/admin/users/{user_id}", headers=admin_headers, json={"name": "新名字"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "新名字"

    assert api.post(f"/api/admin/users/{user_id}/disable", headers=admin_headers).status_code == 200
    assert api.post(f"/api/admin/users/{user_id}/enable", headers=admin_headers).status_code == 200
    assert (
        api.post(
            f"/api/admin/users/{user_id}/reset-password", headers=admin_headers, json={"new_password": "newpw123"}
        ).status_code
        == 200
    )
    assert (
        api.post(f"/api/admin/users/{user_id}/groups", headers=admin_headers, json={"group_ids": []}).status_code == 200
    )
    assert api.post(f"/api/admin/users/{user_id}/approve", headers=admin_headers).status_code == 400  # 非待审批

    assert api.delete(f"/api/admin/users/{user_id}", headers=admin_headers).status_code == 204
    assert api.get("/api/admin/users", headers=admin_headers).json()["total"] == 1  # 只剩超管


def test_admin_cannot_create_admin_account_and_import_template(api):
    init_db()
    with SessionLocal() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        dept_admin = _mk_user(db, "dept@example.com", "dept_admin", dept_group_id=group.id)
        db.commit()
        headers = _headers(dept_admin)

    forbidden = api.post(
        "/api/admin/users",
        headers=headers,
        json={"email": "boss@example.com", "role": "super_admin", "password": "pw123456"},
    )
    assert forbidden.status_code == 403

    template = api.get("/api/admin/users/import/template", headers=headers)
    assert template.status_code == 200
    assert template.headers["content-type"].startswith("application/vnd.openxmlformats")


def test_dept_admin_scope_limits_user_list(api):
    init_db()
    with SessionLocal() as db:
        own = Group(name="本部门", type="部门")
        other = Group(name="其他部门", type="部门")
        db.add_all([own, other])
        db.flush()
        admin = _mk_user(db, "dept2@example.com", "dept_admin", dept_group_id=own.id)
        _mk_user(db, "mine@example.com", "user", dept_group_id=own.id)
        _mk_user(db, "theirs@example.com", "user", dept_group_id=other.id)
        db.commit()
        headers = _headers(admin)

    listed = api.get("/api/admin/users", headers=headers)
    assert listed.status_code == 200
    emails = {item["email"] for item in listed.json()["items"]}
    assert "mine@example.com" in emails
    assert "theirs@example.com" not in emails


def test_dept_admin_cannot_create_user_with_out_of_scope_group(api):
    """dept_admin 新增用户时把 group_ids 指到范围外分组必须 403。

    守卫（`api/users.py::create_user` 的逐项 `gid not in scope`）此前没有任何用例驱动：
    只在服务层测过导入路径，HTTP 直连新增这条完全没覆盖。
    """
    init_db()
    headers, rd_id, mk_id, _team_id = _seed_two_depts()

    forbidden = api.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "outside@example.com",
            "name": "越权用户",
            "role": "user",
            "password": "pw123456",
            "group_ids": [mk_id],
        },
    )
    assert forbidden.status_code == 403, forbidden.text
    # 拒绝必须无副作用：不得留下半个用户
    assert api.get("/api/admin/users", headers=headers, params={"keyword": "outside"}).json()["total"] == 0

    # 对照：分配到本部门子树内的分组必须放行（否则守卫被写成「一律拒绝」也测不出）
    ok = api.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "inside@example.com",
            "name": "本部门用户",
            "role": "user",
            "password": "pw123456",
            "group_ids": [rd_id],
        },
    )
    assert ok.status_code == 201, ok.text


# ---------- 题库 / 题目 ----------
def test_admin_question_bank_and_question_crud(api):
    init_db()
    _admin_id, headers = _seed_admin()

    bank = api.post("/api/admin/question-banks", headers=headers, json={"name": "网络库", "practice_enabled": True})
    assert bank.status_code == 201
    bank_id = bank.json()["id"]

    assert api.get("/api/admin/question-banks", headers=headers).status_code == 200
    assert (
        api.put(f"/api/admin/question-banks/{bank_id}", headers=headers, json={"practice_enabled": False}).status_code
        == 200
    )

    question = api.post(
        "/api/admin/questions",
        headers=headers,
        json={
            "bank_id": bank_id,
            "type": "单选题",
            "question": "HTTP 默认端口?",
            "options": ["21", "80"],
            "answer": "B",
            "score": 2,
        },
    )
    assert question.status_code == 201
    question_id = question.json()["id"]

    listed = api.get("/api/admin/questions", headers=headers, params={"keyword": "端口"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    stats = api.get("/api/admin/question-type-stats", headers=headers, params={"bank_ids": str(bank_id)})
    assert stats.status_code == 200
    assert stats.json()["单选题"] == 1

    assert api.put(f"/api/admin/questions/{question_id}", headers=headers, json={"score": 4}).status_code == 200
    assert api.delete(f"/api/admin/questions/{question_id}", headers=headers).status_code == 204
    assert api.delete(f"/api/admin/question-banks/{bank_id}", headers=headers).status_code == 204


def test_admin_upload_template_and_preview(api):
    init_db()
    _admin_id, headers = _seed_admin()

    template = api.get("/api/admin/upload/template", headers=headers)
    assert template.status_code == 200

    bad = api.post(
        "/api/admin/upload/preview",
        headers=headers,
        files={"file": ("q.txt", b"hello", "text/plain")},
    )
    assert bad.status_code == 400  # 扩展名不在允许列表


def test_question_routes_require_admin(api):
    init_db()
    with SessionLocal() as db:
        user = _mk_user(db, "plain@example.com", "user")
        db.commit()
        headers = _headers(user)

    assert api.get("/api/admin/questions", headers=headers).status_code == 403
    assert api.get("/api/admin/question-banks", headers=headers).status_code == 403


# ---------- 分组 ----------
def test_admin_group_crud_flow(api):
    init_db()
    _admin_id, headers = _seed_admin()

    parent = api.post("/api/admin/groups", headers=headers, json={"name": "总部", "type": "部门"})
    assert parent.status_code == 201
    parent_id = parent.json()["id"]

    child = api.post(
        "/api/admin/groups", headers=headers, json={"name": "研发部", "type": "部门", "parent_id": parent_id}
    )
    assert child.status_code == 201
    child_id = child.json()["id"]

    tree = api.get("/api/admin/groups", headers=headers)
    assert tree.status_code == 200
    assert tree.json()[0]["children"][0]["id"] == child_id

    assert api.put(f"/api/admin/groups/{child_id}", headers=headers, json={"name": "研发中心"}).status_code == 200
    # 有子分组 → 拒绝删除父分组
    assert api.delete(f"/api/admin/groups/{parent_id}", headers=headers).status_code == 400
    assert api.delete(f"/api/admin/groups/{child_id}", headers=headers).status_code == 204
    assert api.delete(f"/api/admin/groups/{parent_id}", headers=headers).status_code == 204


def test_group_create_rejects_invalid_parent_and_type(api):
    init_db()
    _admin_id, headers = _seed_admin()

    assert api.post("/api/admin/groups", headers=headers, json={"name": "x", "type": "外星类型"}).status_code == 400
    assert api.post("/api/admin/groups", headers=headers, json={"name": "x", "parent_id": 99999}).status_code == 400


def test_dept_admin_cannot_create_group_outside_scope(api):
    """dept_admin 只能在自己子树内建子分组：范围外父分组与建到根都必须 403。

    `api/groups.py::create` 的 `parent_id is None or parent_id not in scope` 守卫此前
    零覆盖 —— 删掉它，部门管理员就能在别的部门下挂分组、或直接在根上开一个新部门。
    """
    init_db()
    headers, rd_id, mk_id, _team_id = _seed_two_depts()

    out_of_scope = api.post(
        "/api/admin/groups", headers=headers, json={"name": "越权子组", "type": "部门", "parent_id": mk_id}
    )
    assert out_of_scope.status_code == 403, out_of_scope.text

    at_root = api.post(
        "/api/admin/groups", headers=headers, json={"name": "越权根组", "type": "部门", "parent_id": None}
    )
    assert at_root.status_code == 403, at_root.text

    # 对照：子树内建子分组必须放行（否则守卫被写成「一律拒绝」也测不出）
    inside = api.post(
        "/api/admin/groups", headers=headers, json={"name": "本部门子组", "type": "部门", "parent_id": rd_id}
    )
    assert inside.status_code == 201, inside.text


def test_dept_admin_cannot_delete_out_of_scope_group(api):
    """dept_admin 删除范围外分组必须 403，范围内分组 204。"""
    init_db()
    headers, _rd_id, mk_id, team_id = _seed_two_depts()

    forbidden = api.delete(f"/api/admin/groups/{mk_id}", headers=headers)
    assert forbidden.status_code == 403, forbidden.text
    # 拒绝必须无副作用：市场部必须还在
    with SessionLocal() as db:
        assert db.get(Group, mk_id) is not None

    # 对照：范围内分组可删（否则守卫被写成「一律拒绝」也测不出）
    assert api.delete(f"/api/admin/groups/{team_id}", headers=headers).status_code == 204


# ---------- 系统设置 ----------
def test_system_settings_read_and_update(api):
    init_db()
    _admin_id, headers = _seed_admin()

    site = api.get("/api/system/site")
    assert site.status_code == 200
    assert "site_name" in site.json()

    assert api.get("/api/system/categories", headers=headers).status_code == 200
    assert api.get("/api/system/settings", headers=headers).status_code == 200
    assert api.get("/api/system/settings", headers=headers, params={"category": "general"}).status_code == 200

    updated = api.put(
        "/api/system/settings",
        headers=headers,
        json={"category": "general", "updates": {"site_name": "新站点"}},
    )
    assert updated.status_code == 200
    assert api.get("/api/system/site").json()["site_name"] == "新站点"

    # 布尔型设置项非法值必须被拒绝（rank_visible 下线后，改用同类的 register_open）
    invalid = api.put(
        "/api/system/settings", headers=headers, json={"category": "register", "updates": {"register_open": "maybe"}}
    )
    assert invalid.status_code == 400


def test_smtp_test_surfaces_success_and_failure(api, monkeypatch):
    init_db()
    _admin_id, headers = _seed_admin()

    from app.services import mail_service

    missing_host = api.post("/api/system/smtp/test", headers=headers, json={"to_email": "a@example.com"})
    assert missing_host.status_code == 400

    api.put(
        "/api/system/settings",
        headers=headers,
        json={"category": "smtp", "updates": {"smtp_host": "smtp.example.com", "smtp_sender": "n@example.com"}},
    )

    monkeypatch.setattr(mail_service, "_send", lambda *args, **kwargs: None)
    ok = api.post("/api/system/smtp/test", headers=headers, json={"to_email": "a@example.com"})
    assert ok.status_code == 200

    def _boom(*_args, **_kwargs):
        raise mail_service.MailError("认证失败")

    monkeypatch.setattr(mail_service, "_send", _boom)
    failed = api.post("/api/system/smtp/test", headers=headers, json={"to_email": "a@example.com"})
    assert failed.status_code == 400
    assert "认证失败" in failed.json()["detail"]


def test_system_routes_require_super_admin(api):
    init_db()
    with SessionLocal() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        dept_admin = _mk_user(db, "dept3@example.com", "dept_admin", dept_group_id=group.id)
        db.commit()
        headers = _headers(dept_admin)

    assert api.get("/api/system/settings", headers=headers).status_code == 403
    assert api.get("/api/system/categories", headers=headers).status_code == 403
    assert api.post("/api/system/smtp/test", headers=headers, json={"to_email": "a@example.com"}).status_code == 403
