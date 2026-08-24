#!/bin/bash
# 培训考试平台 一键启动：构建前端 → 初始化数据库 → 启动后端
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "[1/3] 构建前端..."
cd "$ROOT/frontend"
if command -v pnpm >/dev/null 2>&1; then
  pnpm install
  pnpm build
else
  npm install
  npm run build
fi

echo "[2/3] 初始化后端依赖与数据库..."
cd "$ROOT/backend"
uv sync
uv run python scripts/init_db.py

echo "[3/3] 启动后端 (http://localhost:8000)..."
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
