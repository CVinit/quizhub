"""FastAPI 入口：挂载路由、CORS、静态资源托管（前端 dist）、SPA fallback。"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.api import audit as audit_router
from app.api import auth as auth_router
from app.api import exams as exams_router
from app.api import groups as groups_router
from app.api import panel as panel_router
from app.api import questions as questions_router
from app.api import records as records_router
from app.api import system as system_router
from app.api import users as users_router
from app.core.errors import DomainError
from app.core.logconfig import configure_logging
from app.core.rate_limit import get_client_ip
from app.core.request_context import get_request_ip, set_request_ip
from app.database import init_db

logger = logging.getLogger("quizhub")

# 公开静态目录下禁止回源的扩展名：SVG 可内嵌 <script>，与站点同源直接导航即执行
# （存储型 XSS）。上传侧已不接受 .svg，这里再兜住历史遗留文件。
_PUBLIC_BLOCKED_SUFFIXES = (".svg",)

# 前端构建产物目录。抽成模块级常量是为了可测试：SPA fallback 与其中的路径遍历守卫
# 只在 `dist` 存在时才注册，测试可用 monkeypatch 指向临时目录来真正覆盖这两个分支
# （原实现把路径写在 create_app 内部，未构建前端的检出里相关用例是恒真的）。
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 启动触发统计预聚合（今日+昨日）
    try:
        from app.database import db_session
        from app.services import stats_service

        with db_session() as db:
            stats_service.startup_refresh(db)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[stats] startup refresh failed: %s", exc)
    yield


def create_app() -> FastAPI:
    # 统一日志出口：否则 logger.info 会被无 handler 的 root 丢弃
    configure_logging()
    app = FastAPI(title="培训考试平台 API", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError):
        """把服务层的领域错误统一映射为 HTTP 响应。

        服务层不再依赖 `fastapi.HTTPException`；响应体与 FastAPI 内建处理器保持一致
        （`{"detail": ...}`），因此前端与既有 API 契约不变。
        """
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        """统一安全响应头，并拦截公开目录中的可执行静态文件。

        原实现无任何安全响应头：`/files` 与 logo 回源是无嗅探保护的同源静态读取，
        配合可上传的 SVG 即构成存储型 XSS 面。这里集中兜底（含历史遗留文件）。

        同时把客户端 IP 写入请求上下文：审计日志在服务层调用，拿不到 Request，
        原先 `audit_service.log(ip=...)` 的形参没有任何调用方传入、该列恒为空。
        """
        set_request_ip(get_client_ip(request))
        path = request.url.path.lower()
        if path.startswith("/files/") and path.endswith(_PUBLIC_BLOCKED_SUFFIXES):
            return PlainTextResponse("Not Found", status_code=404)
        try:
            response = await call_next(request)
        except Exception:
            # 5xx 此前只留 uvicorn 的裸堆栈：这里补一条带方法/路径/客户端 IP 的结构化
            # 日志（不含请求体与 PII），便于线上定位；异常照旧向上抛，由 Starlette 转 500。
            logger.exception(
                "[api] 未处理异常 method=%s path=%s ip=%s",
                request.method,
                request.url.path,
                get_request_ip(),
            )
            raise
        if response.status_code >= 500:
            logger.error(
                "[api] 服务端错误 method=%s path=%s status=%s",
                request.method,
                request.url.path,
                response.status_code,
            )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    # 逗号分隔的来源需逐个 strip：否则 `https://a.com, https://b.com` 的第二项带前导空格，
    # Starlette 精确匹配失败、该来源被静默拒绝。
    cors_origins = [origin.strip() for origin in os.getenv("TRAINING_CORS_ORIGINS", "*").split(",") if origin.strip()]
    if not cors_origins:
        cors_origins = ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        # 通配符来源时禁止凭据，避免反射 Origin 的跨域已认证请求攻击
        allow_credentials="*" not in cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = app
    api.include_router(auth_router.router, prefix="/api")
    api.include_router(groups_router.router, prefix="/api")
    api.include_router(users_router.router, prefix="/api")
    api.include_router(questions_router.router, prefix="/api")
    api.include_router(records_router.router, prefix="/api")
    api.include_router(exams_router.router, prefix="/api")
    api.include_router(panel_router.router, prefix="/api")
    api.include_router(system_router.router, prefix="/api")
    api.include_router(audit_router.router, prefix="/api")

    # 站点静态文件托管（Logo 等上传文件，公开可读）
    from app.config import FILES_DIR

    if FILES_DIR.exists():
        app.mount("/files", StaticFiles(directory=FILES_DIR), name="files")

    # 前端静态资源托管（dist 构建产物）
    dist = FRONTEND_DIST
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        for sub in ("img", "fonts"):
            sub_path = dist / sub
            if sub_path.exists():
                app.mount(f"/{sub}", StaticFiles(directory=sub_path), name=sub)

        @app.get("/")
        def _index():
            return FileResponse(dist / "index.html")

        # SPA fallback：非 /api 路径回退到 index.html
        # 安全：校验解析后路径必须在 dist 目录内，杜绝 ../ 路径遍历
        dist_resolved = dist.resolve()

        @app.get("/{full_path:path}")
        def _spa(full_path: str):
            # 未匹配的 /api 路径必须 404：原实现返回 200 + {"detail": "Not Found"}，
            # 会让客户端与探活/监控把不存在的接口当成成功响应。
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
            # 拒绝显式相对路径片段
            if ".." in full_path.split("/"):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
            target = (dist / full_path).resolve()
            try:
                target.relative_to(dist_resolved)
            except ValueError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found") from None
            if target.is_file():
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
