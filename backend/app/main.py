"""FastAPI 入口：挂载路由、CORS、静态资源托管（前端 dist）、SPA fallback。"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
from app.database import init_db

logger = logging.getLogger("quizhub")


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
    app = FastAPI(title="培训考试平台 API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("TRAINING_CORS_ORIGINS", "*").split(","),
        # 通配符来源时禁止凭据，避免反射 Origin 的跨域已认证请求攻击
        allow_credentials=os.getenv("TRAINING_CORS_ORIGINS", "*") != "*",
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
    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
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
            if full_path.startswith("api"):
                return {"detail": "Not Found"}
            # 拒绝显式相对路径片段
            if ".." in full_path.split("/"):
                return {"detail": "Not Found"}
            target = (dist / full_path).resolve()
            try:
                target.relative_to(dist_resolved)
            except ValueError:
                return {"detail": "Not Found"}
            if target.is_file():
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
