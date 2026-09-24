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


@pytest.fixture
def spa_client(tmp_path, monkeypatch):
    """客户端 + 临时前端 dist，用于覆盖 SPA fallback 与其中的路径遍历守卫。

    仓库里的 `frontend/dist` 是构建产物且被 gitignore，CI 只装后端 —— 不 monkeypatch
    时 `create_app()` 压根不会注册 `/{full_path:path}` 路由，任何 `/api/...` 未匹配路径
    都由 Starlette 默认处理器返回 404：原用例于是恒真，删掉生产守卫也照样通过。
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)  # create_app 会无条件 mount /assets
    (dist / "index.html").write_text("<!doctype html><title>SPA-FALLBACK</title>", encoding="utf-8")
    monkeypatch.setattr("app.main.FRONTEND_DIST", dist)

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
        yield test_client, dist


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


def test_unknown_api_path_returns_404_not_200(spa_client):
    """未匹配的 /api 路径必须 404（原实现返回 200 + {"detail": "Not Found"}）。

    必须挂上 SPA fallback 才有意义：`/{full_path:path}` 会把未匹配的 `/api/*` 吞掉，
    生产守卫就在该路由里；没有 dist 时它不注册，本用例无法覆盖到被测代码。
    """
    test_client, _dist = spa_client
    init_db()

    response = test_client.get("/api/definitely-not-a-route")

    assert response.status_code == 404


def test_spa_fallback_serves_index_and_blocks_traversal(spa_client):
    """非 /api 的未知前端路由回退 index.html；任何路径遍历一律 404。

    SPA 客户端路由依赖回退（否则刷新 /exam/1 会 404），但回退实现里
    `target.relative_to(dist_resolved)` 的越界守卫与 `..` 显式拒绝都不能被绕过。
    """
    test_client, dist = spa_client
    init_db()

    # 未知前端路由 → 200 且返回 index.html
    spa = test_client.get("/some/spa/route")
    assert spa.status_code == 200, spa.text
    assert "SPA-FALLBACK" in spa.text

    # dist 内的真实文件仍按文件返回（回退不能把静态资源也换成 index.html）
    asset = dist / "assets" / "app.js"
    asset.write_text("console.log(1)", encoding="utf-8")
    assert "SPA-FALLBACK" not in test_client.get("/assets/app.js").text

    # 目录遍历：URL 编码的 ../ 与 %2e%2e 均须 404，不得读出 dist 之外的文件
    for traversal in (
        "/..%2f..%2fetc%2fpasswd",
        "/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "/..%2fsecret.txt",
        "/a/..%2f..%2fetc%2fpasswd",
    ):
        resp = test_client.get(traversal)
        assert resp.status_code == 404, f"{traversal} → {resp.status_code}"


def test_spa_fallback_dotdot_guard_rejects_parent_segments(spa_client):
    """解码后含 `..` 片段的路径必须 404，不能只靠 relative_to 兜底。

    必须用 `%2e%2e`（编码后的点）而不是字面 `..`：httpx/URL 标准会先做点段归一化，
    字面 `..` 到不了服务端（`/assets/../index.html` 会被客户端改写成 `/index.html`），
    那样测的就不是生产守卫而是客户端行为。编码形式到服务端才解码成 `..`：
    - `/assets/%2e%2e/index.html` → 显式守卫命中 → 404；
      若删掉 `".." in full_path.split("/")`，它会解析回 dist 内的 index.html 而返回 200。
    - `/%2e%2e/secret.txt` → 解析后越出 dist → relative_to 兜底 → 404。
    """
    test_client, dist = spa_client
    init_db()
    # 在 dist 外放一个可读文件：越界请求即使被解析也不得读出内容
    (dist.parent / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")

    for traversal in (
        "/assets/%2e%2e/index.html",
        "/a/%2e%2e/%2e%2e/index.html",
        "/%2e%2e/secret.txt",
    ):
        resp = test_client.get(traversal)
        assert resp.status_code == 404, f"{traversal} → {resp.status_code}"
        assert "TOP-SECRET" not in resp.text
