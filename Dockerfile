# syntax=docker/dockerfile:1

# ---------- 阶段 1：前端构建 ----------
# dist 为纯静态文件，固定在构建机原生平台执行，不参与 QEMU 跨架构模拟
FROM --platform=$BUILDPLATFORM node:20-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 阶段 2：后端依赖安装（按目标平台装进 .venv）----------
FROM python:3.12-slim AS backend-deps
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /opt/quizhub/backend
# 先只拷贝依赖清单，源码变更不破坏依赖层缓存
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---------- 阶段 3：运行时 ----------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/quizhub/backend

# 虚拟环境与源码保持与构建阶段相同的绝对路径：
# main.py 按 <根>/frontend/dist 的相对布局托管前端产物
COPY --from=backend-deps /opt/quizhub/backend/.venv /opt/quizhub/backend/.venv
ENV PATH="/opt/quizhub/backend/.venv/bin:$PATH"

COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/app ./app
COPY backend/scripts ./scripts
COPY --from=frontend-build /build/dist /opt/quizhub/frontend/dist

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/healthcheck.py /usr/local/bin/healthcheck.py

# 非 root 运行（uid/gid 1000，与多数 Linux 宿主机的首个用户一致，便于绑定挂载数据卷）
RUN groupadd --gid 1000 quizhub \
    && useradd --uid 1000 --gid quizhub --create-home quizhub \
    && chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /opt/quizhub/backend/data \
    && chown -R quizhub:quizhub /opt/quizhub

USER quizhub

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "/usr/local/bin/healthcheck.py"]

# 启动前幂等执行 init_db（建表/默认设置/超管）与增量迁移，再启动 uvicorn
ENTRYPOINT ["entrypoint.sh"]
