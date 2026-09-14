# 培训考试平台 一键启动（使用项目内嵌 Python，无需 uv / 系统 Python / 虚拟环境）
#
# 用法：
#   PowerShell：  ./start-embed.ps1
#   双击运行：     start-embed.bat
#   指定端口：     ./start-embed.ps1 -Port 9000
#   强制重建前端： ./start-embed.ps1 -Rebuild
param(
    [int]$Port = 8000,
    [switch]$Rebuild,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$py = Join-Path $PSScriptRoot "python-3.12.10-embed-amd64\python.exe"
$backend = Join-Path $PSScriptRoot "backend"
$frontend = Join-Path $PSScriptRoot "frontend"
$dist = Join-Path $frontend "dist"
$keyFile = Join-Path $backend "data\.dev-secrets.env"

if (-not (Test-Path -LiteralPath $py)) {
    Write-Host "[错误] 未找到内嵌 Python：$py" -ForegroundColor Red
    exit 1
}

# JWT / 加密密钥：首次运行生成并落盘复用，避免每次重启后登录态失效
if (-not (Test-Path -LiteralPath $keyFile)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $keyFile) | Out-Null
    $generated = & $py -c "import base64,secrets;print('TRAINING_SECRET_KEY='+secrets.token_urlsafe(48));print('TRAINING_ENC_KEY='+base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
    Set-Content -LiteralPath $keyFile -Value $generated -Encoding utf8
}
Get-Content -LiteralPath $keyFile | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}

Write-Host "[1/4] 检查后端依赖..."
& $py -c "import fastapi, uvicorn, sqlalchemy, pydantic, jose, bcrypt, openpyxl, cryptography, email_validator" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "        依赖缺失，正在从 PyPI 安装到内嵌目录（首次较慢）..."
    $depsJson = & $py -c "import sys,tomllib;print(' '.join(tomllib.load(open(sys.argv[1],'rb'))['project']['dependencies']))" (Join-Path $backend "pyproject.toml")
    $deps = @($depsJson -split '\s+' | Where-Object { $_ })
    & $py -m pip install --no-warn-script-location @deps
    if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 后端依赖安装失败" -ForegroundColor Red; exit 1 }
}

Write-Host "[2/4] 准备前端静态资源..."
if ($Rebuild -or -not (Test-Path -LiteralPath (Join-Path $dist "index.html"))) {
    Push-Location $frontend
    try {
        if (Get-Command pnpm -ErrorAction SilentlyContinue) {
            pnpm install; if ($LASTEXITCODE -eq 0) { pnpm build }
        } elseif (Get-Command npm -ErrorAction SilentlyContinue) {
            npm install; if ($LASTEXITCODE -eq 0) { npm run build }
        } else {
            Write-Host "[警告] 未检测到 pnpm/npm，跳过前端构建，仅提供 API" -ForegroundColor Yellow
            $LASTEXITCODE = 0
        }
        if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 前端构建失败" -ForegroundColor Red; exit 1 }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "        dist 已存在，跳过构建（-Rebuild 可强制重建）"
}

Write-Host "[3/4] 初始化数据库..."
Push-Location $backend
try {
    & $py "scripts\init_db.py"
    if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 数据库初始化失败" -ForegroundColor Red; exit 1 }
    & $py "scripts\migrate_2026_08_28.py"
    if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 数据库迁移失败" -ForegroundColor Red; exit 1 }

    Write-Host "[4/4] 启动后端 → http://127.0.0.1:$Port  (Ctrl+C 停止)"
    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
    & $py -m uvicorn app.main:app --host 0.0.0.0 --port $Port
} finally {
    Pop-Location
}
