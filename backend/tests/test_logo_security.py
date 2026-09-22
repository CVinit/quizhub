"""Logo 上传/回源与公开静态目录的安全加固回归。

背景（E3）：`/files` 与 logo 回源都是**同源公开读取**，且原先允许上传 `.svg`。
浏览器直接导航 `/files/logo.svg` 即执行 SVG 内嵌 `<script>` —— 存储型 XSS。
原实现还先 `await file.read()` 再比较大小，超大 body 已先进入内存。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.database import SessionLocal, get_db, init_db
from app.main import create_app
from app.models.user import User

_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _token_for_super_admin() -> str:
    with SessionLocal() as db:
        user = User(
            email="logo-admin@example.com",
            password_hash="x",
            name="超管",
            role="super_admin",
            status="active",
            email_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return create_access_token(user.id, {"role": user.role, "ver": user.token_version})


@pytest.fixture
def client(tmp_path, monkeypatch):
    """客户端 + 隔离的 Logo 目录。

    `app.config.FILES_DIR` / `app.api.system.FILES_DIR` 都指向临时目录，
    避免测试把文件写进仓库的 backend/data/files。
    """
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
    with TestClient(app) as test_client:
        yield test_client, files_dir


def _upload(client: TestClient, token: str, filename: str, content: bytes, content_type: str):
    return client.post(
        "/api/system/logo",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, content, content_type)},
    )


def test_svg_logo_upload_is_rejected_and_legacy_svg_is_not_served(client):
    test_client, files_dir = client
    token = _token_for_super_admin()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(document.domain)</script></svg>'

    response = _upload(test_client, token, "logo.svg", svg, "image/svg+xml")

    assert response.status_code == 400
    assert not (files_dir / "logo.svg").exists()

    # 历史遗留的 svg 即使已存在于目录中，也绝不从公开路径回源
    (files_dir / "legacy.svg").write_text("<svg/>", encoding="utf-8")
    assert test_client.get("/files/legacy.svg").status_code == 404
    assert test_client.get("/api/system/logo/logo.svg").status_code == 404
    assert test_client.get("/api/system/logo/legacy.svg").status_code == 404


def test_png_logo_upload_served_with_nosniff_and_replaces_other_extensions(client):
    test_client, files_dir = client
    token = _token_for_super_admin()
    stale = files_dir / "logo.jpg"
    stale.write_bytes(b"stale")

    response = _upload(test_client, token, "brand.png", _PNG_1PX, "image/png")

    assert response.status_code == 200
    assert response.json()["url"] == "/files/logo.png"
    # 固定文件名覆盖旧 Logo，并清掉历史遗留的其它扩展名（含可能可执行的 svg）
    assert (files_dir / "logo.png").exists()
    assert not stale.exists()

    static = test_client.get("/files/logo.png")
    assert static.status_code == 200
    assert static.headers["content-type"].startswith("image/png")
    assert static.headers["x-content-type-options"] == "nosniff"

    endpoint = test_client.get("/api/system/logo/logo.png")
    assert endpoint.status_code == 200
    assert endpoint.headers["content-type"].startswith("image/png")
    assert endpoint.headers["x-content-type-options"] == "nosniff"


def test_logo_upload_rejects_oversized_body(client):
    test_client, files_dir = client
    token = _token_for_super_admin()
    too_big = b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024)

    response = _upload(test_client, token, "big.png", too_big, "image/png")

    assert response.status_code == 413
    assert not (files_dir / "logo.png").exists()


def test_logo_endpoint_rejects_non_logo_names(client):
    test_client, _files_dir = client
    # 路径遍历与非 logo 前缀一律 404（basename 先剥离目录）
    assert test_client.get("/api/system/logo/..%2Ftraining.db").status_code == 404
    assert test_client.get("/api/system/logo/secret.png").status_code == 404


def test_security_headers_present_on_normal_responses(client):
    test_client, _files_dir = client

    response = test_client.get("/api/system/site")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["referrer-policy"] == "same-origin"


def test_unknown_api_path_returns_404_not_200(client):
    """未匹配的 /api 路径必须 404（原实现返回 200 + {"detail": "Not Found"}）。"""
    test_client, _files_dir = client
    init_db()

    response = test_client.get("/api/definitely-not-a-route")

    assert response.status_code == 404
