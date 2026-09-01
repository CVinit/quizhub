#!/bin/sh
# 容器入口：幂等初始化/迁移数据库后启动 uvicorn（SQLite 单进程架构）
set -e

cd /opt/quizhub/backend

echo "[entrypoint] 初始化数据库（建表/默认设置/超管，幂等）..."
python scripts/init_db.py
echo "[entrypoint] 执行增量迁移（幂等）..."
python scripts/migrate_2026_08_28.py

echo "[entrypoint] 启动服务：0.0.0.0:${PORT:-8000}"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
