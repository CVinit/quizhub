"""管理端导入 HTTP 层 + 审计日志数据范围。

报告第五节的缺口：
- `/api/admin/upload/preview|import` 与 `/api/admin/users/import/preview|import` 只测到服务层
  （或只测扩展名拒绝），分块读取、体积上限、越权不消费 token 等 HTTP 行为无覆盖；
- 审计日志只有 200 冒烟，dept_admin 的范围过滤与系统日志可见性没有断言。
"""

from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.security import create_access_token
from app.database import SessionLocal, db_session, get_db, init_db
from app.main import create_app
from app.models.group import Group
from app.models.user import User
from app.services import audit_service, system_service
from app.utils import user_excel
from app.utils.excel import HEADERS

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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


def _seed() -> dict:
    """建一个部门 + 三种角色用户，返回鉴权头与 id。"""
    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        out: dict = {"group_id": group.id}
        for role, email in (
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
            out[f"{role}_id"] = user.id
            out[f"{role}_headers"] = {
                "Authorization": f"Bearer {create_access_token(user.id, {'ver': user.token_version})}"
            }
        db.commit()
        return out


def _question_workbook(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "单选题"
    ws.append(HEADERS["单选题"])
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _user_workbook(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "用户"
    ws.append(user_excel.HEADERS)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------- 题库导入 HTTP 层 ----------
def test_question_upload_preview_then_import_over_http(api):
    headers = _seed()["super_admin_headers"]
    files = {"file": ("q.xlsx", _question_workbook([["题一", "A.甲\nB.乙", "A", "解析", 2, "标签", 2, ""]]), XLSX_MIME)}

    preview = api.post("/api/admin/upload/preview", files=files, headers=headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["valid_count"] == 1
    assert preview.json()["truncated"] is False

    imported = api.post(
        "/api/admin/upload/import",
        data={"confirm_token": preview.json()["confirm_token"]},
        headers=headers,
    )
    assert imported.status_code == 200, imported.text
    assert imported.json() == {"success": 1, "failed": 0}


def test_question_upload_preview_rejects_oversize(api):
    headers = _seed()["super_admin_headers"]
    # 上限设为 0：任何非空上传都必须 413（等价于极小上限，避免造大文件）
    with db_session() as db:
        system_service.update_settings(db, "upload", {"upload_max_size_mb": "0"})

    files = {"file": ("q.xlsx", _question_workbook([["题一", "A.甲\nB.乙", "A", "", 2, "", 2, ""]]), XLSX_MIME)}
    resp = api.post("/api/admin/upload/preview", files=files, headers=headers)
    assert resp.status_code == 413, resp.text


# ---------- 用户导入 HTTP 层 ----------
def test_user_import_preview_then_import_over_http(api):
    headers = _seed()["super_admin_headers"]
    files = {
        "file": (
            "u.xlsx",
            _user_workbook([["new@quizhub.com", "新用户", "普通用户", "pw123456", "正常", ""]]),
            XLSX_MIME,
        )
    }

    preview = api.post("/api/admin/users/import/preview", files=files, headers=headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["valid_count"] == 1
    assert "password" not in preview.json()["rows"][0], "预览不得回传明文初始口令"

    imported = api.post(
        "/api/admin/users/import",
        data={"confirm_token": preview.json()["confirm_token"]},
        headers=headers,
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["success"] == 1


def test_user_import_preview_rejects_non_xlsx(api):
    headers = _seed()["super_admin_headers"]
    files = {"file": ("u.txt", b"not a workbook", "text/plain")}
    resp = api.post("/api/admin/users/import/preview", files=files, headers=headers)
    assert resp.status_code == 400, resp.text


def test_dept_admin_cannot_import_admin_rows_and_token_survives(api):
    """越权导入管理员行必须 403，且不得消费上传者本人的预览 token。"""
    ids = _seed()
    files = {
        "file": (
            "u.xlsx",
            _user_workbook([["boss@quizhub.com", "管理员", "部门管理员", "pw123456", "正常", ""]]),
            XLSX_MIME,
        )
    }

    preview = api.post("/api/admin/users/import/preview", files=files, headers=ids["dept_admin_headers"])
    assert preview.status_code == 200, preview.text
    token = preview.json()["confirm_token"]

    denied = api.post("/api/admin/users/import", data={"confirm_token": token}, headers=ids["dept_admin_headers"])
    assert denied.status_code == 403, denied.text

    # 越权尝试不得消费 token：再次提交仍是「角色拒绝」（403），而不是「预览已过期」（400）
    again = api.post("/api/admin/users/import", data={"confirm_token": token}, headers=ids["dept_admin_headers"])
    assert again.status_code == 403, again.text
    assert "超级管理员" in again.json()["detail"]


def test_user_preview_marks_truncation(monkeypatch):
    """超过单次导入上限时不再静默截断：返回 truncated 标记 + 一条可见错误。"""
    monkeypatch.setattr(user_excel, "_IMPORT_ROW_MAX", 1)
    content = _user_workbook(
        [
            ["a@quizhub.com", "甲", "普通用户", "pw123456", "正常", ""],
            ["b@quizhub.com", "乙", "普通用户", "pw123456", "正常", ""],
        ]
    )
    preview = user_excel.preview(content, user_id=1)
    assert preview["truncated"] is True
    assert preview["valid_count"] == 1
    assert any("上限" in item["error"] for item in preview["errors"])


def test_user_preview_reports_missing_sheet():
    """工作簿里没有「用户」Sheet 时给出可见错误，而不是静默返回 0 行。"""
    wb = Workbook()
    wb.active.title = "别的表"
    buf = BytesIO()
    wb.save(buf)

    preview = user_excel.preview(buf.getvalue(), user_id=1)
    assert preview["valid_count"] == 0
    assert any("用户" in item["error"] for item in preview["errors"])


# ---------- 审计日志数据范围 ----------
def test_audit_logs_are_scoped_for_dept_admin(api):
    ids = _seed()
    with db_session() as db:
        audit_service.log(db, ids["dept_admin_id"], "user.update", "user", "1")
        audit_service.log(db, ids["super_admin_id"], "exam.create", "exam", "1")
        audit_service.log(db, None, "system.migrate", "system", "1")

    full = api.get("/api/admin/audit-logs", headers=ids["super_admin_headers"])
    assert full.status_code == 200, full.text
    assert full.json()["total"] == 3, "super_admin 应看到全部日志（含系统日志）"

    scoped = api.get("/api/admin/audit-logs", headers=ids["dept_admin_headers"])
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["total"] == 1, "dept_admin 只应看到范围内操作者的日志"
    assert all(f"#{ids['dept_admin_id']}" in item["actor"] for item in scoped.json()["items"])
