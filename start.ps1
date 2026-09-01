# 培训考试平台 一键启动（Windows / PowerShell）
# 等价于 start.sh。需预装：Python 3.10+、uv、Node.js 18+、pnpm（或 npm）。
#
# 用法：
#   PowerShell 中执行：  ./start.ps1
# 若提示执行策略受限，先运行：  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
$ErrorActionPreference = "Stop"

# 切换到脚本所在目录
Set-Location -Path $PSScriptRoot

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

if (-not (Test-Command "uv")) {
    Write-Host "[错误] 未检测到 uv，请先安装：" -ForegroundColor Red
    Write-Host '  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
    Read-Host "按回车退出"; exit 1
}

Write-Host "[1/3] 构建前端..."
Set-Location "$PSScriptRoot/frontend"
if (Test-Command "pnpm") {
    pnpm install
    pnpm build
} else {
    npm install
    npm run build
}
if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 前端构建失败" -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }

Write-Host "[2/3] 初始化后端依赖与数据库..."
Set-Location "$PSScriptRoot/backend"
uv sync
if ($LASTEXITCODE -ne 0) { Write-Host "[错误] uv sync 失败" -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }
uv run python scripts/init_db.py
if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 数据库初始化失败" -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }
uv run python scripts/migrate_2026_08_28.py
if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 数据库迁移失败" -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }

Write-Host "[3/3] 启动后端 (http://localhost:8000)..."
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
