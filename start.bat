@echo off
REM 培训考试平台 一键启动（Windows）：构建前端 → 初始化数据库 → 启动后端
REM 等价于 start.sh。需预装：Python 3.10+、uv、Node.js 18+、pnpm（或 npm）。
setlocal enabledelayedexpansion

REM 切换到脚本所在目录
cd /d "%~dp0"

where uv >nul 2>&1
if errorlevel 1 (
  echo [错误] 未检测到 uv，请先安装：powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
  pause
  exit /b 1
)

echo [1/3] 构建前端...
cd /d "%~dp0frontend"
where pnpm >nul 2>&1
if errorlevel 1 (
  REM 未装 pnpm 则回退 npm
  call npm install
  if errorlevel 1 ( echo [错误] npm install 失败 & pause & exit /b 1 )
  call npm run build
  if errorlevel 1 ( echo [错误] 前端构建失败 & pause & exit /b 1 )
) else (
  call pnpm install
  if errorlevel 1 ( echo [错误] pnpm install 失败 & pause & exit /b 1 )
  call pnpm build
  if errorlevel 1 ( echo [错误] 前端构建失败 & pause & exit /b 1 )
)

echo [2/3] 初始化后端依赖与数据库...
cd /d "%~dp0backend"
uv sync
if errorlevel 1 ( echo [错误] uv sync 失败 & pause & exit /b 1 )
uv run python scripts\init_db.py
if errorlevel 1 ( echo [错误] 数据库初始化失败 & pause & exit /b 1 )

echo [3/3] 启动后端 (http://localhost:8000)...
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
endlocal
