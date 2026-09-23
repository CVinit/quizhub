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

# 运行时必需的依赖（pyproject 之外还需 tzdata，否则内嵌 Python 无法解析
# Asia/Shanghai 时区，业务时间会静默回退 UTC 导致整体偏移 8 小时）
$extraDeps = @("tzdata")

Write-Host "[1/4] 检查后端依赖..."
& $py -c "import fastapi, uvicorn, sqlalchemy, pydantic, pydantic_settings, jose, bcrypt, multipart, openpyxl, cryptography, email_validator, tzdata" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "        依赖缺失，正在从 PyPI 安装到内嵌目录（首次较慢）..."
    $depsJson = & $py -c "import sys,tomllib;print(' '.join(tomllib.load(open(sys.argv[1],'rb'))['project']['dependencies']))" (Join-Path $backend "pyproject.toml")
    $deps = @($depsJson -split '\s+' | Where-Object { $_ }) + $extraDeps
    & $py -m pip install --no-warn-script-location @deps
    if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 后端依赖安装失败" -ForegroundColor Red; exit 1 }
} else {
    # 即便核心依赖齐全，也确保 tzdata 已就位（早期版本可能缺失）
    & $py -c "import tzdata" 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $py -m pip install --no-warn-script-location @extraDeps
    }
}

Write-Host "[2/4] 准备前端静态资源..."

# 判断 dist 是否需要重建：只要 src/ 下的源码、或构建配置比 dist/index.html 新，
# 就重建。原实现只看「dist/index.html 是否存在」，导致改完前端不重建就一直是旧界面。
function Test-FrontendNeedsBuild {
    $indexHtml = Join-Path $dist "index.html"
    if (-not (Test-Path -LiteralPath $indexHtml)) { return $true }
    $builtAt = (Get-Item -LiteralPath $indexHtml).LastWriteTimeUtc

    # 参与构建的输入：源码、静态资源、构建配置与依赖清单
    $watched = @()
    foreach ($dir in @("src", "public")) {
        $p = Join-Path $frontend $dir
        if (Test-Path -LiteralPath $p) {
            $watched += Get-ChildItem -LiteralPath $p -Recurse -File -ErrorAction SilentlyContinue
        }
    }
    foreach ($f in @("index.html", "package.json", "vite.config.ts", "tsconfig.json")) {
        $p = Join-Path $frontend $f
        if (Test-Path -LiteralPath $p) { $watched += Get-Item -LiteralPath $p }
    }
    # node_modules 缺失时也必须重建（依赖未安装时 dist 不可信）
    if (-not (Test-Path -LiteralPath (Join-Path $frontend "node_modules"))) { return $true }

    return ($watched | Where-Object { $_.LastWriteTimeUtc -gt $builtAt } | Select-Object -First 1) -ne $null
}

$needBuild = $Rebuild -or (Test-FrontendNeedsBuild)
if ($needBuild) {
    if ($Rebuild) {
        Write-Host "        -Rebuild 指定，强制重建"
    } else {
        Write-Host "        检测到前端源码/配置有更新，重新构建"
    }
    Push-Location $frontend
    try {
        if (Get-Command pnpm -ErrorAction SilentlyContinue) {
            pnpm install; if ($LASTEXITCODE -eq 0) { pnpm build }
            if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 前端构建失败" -ForegroundColor Red; exit 1 }
        } elseif (Get-Command npm -ErrorAction SilentlyContinue) {
            npm install; if ($LASTEXITCODE -eq 0) { npm run build }
            if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 前端构建失败" -ForegroundColor Red; exit 1 }
        } else {
            # 无构建工具时跳过（不再检查 $LASTEXITCODE：它可能残留上一条命令的非零值，
            # 会误报"构建失败"）
            Write-Host "[警告] 未检测到 pnpm/npm，跳过前端构建，仅提供 API" -ForegroundColor Yellow
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "        dist 已是最新，跳过构建（-Rebuild 可强制重建）"
}

Write-Host "[3/4] 初始化数据库..."
Push-Location $backend
try {
    & $py "scripts\init_db.py"
    if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 数据库初始化失败" -ForegroundColor Red; exit 1 }
    # 按文件名顺序跑全部迁移脚本：原来只硬编码 08_28，新增的 09_14 从未被执行，
    # 导致既有库缺少 practice_enabled 列。迁移脚本均为幂等，可重复运行。
    $migrations = Get-ChildItem -LiteralPath "scripts" -Filter "migrate_*.py" -File | Sort-Object Name
    foreach ($m in $migrations) {
        Write-Host "        执行 $($m.Name) ..."
        & $py "scripts\$($m.Name)"
        if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 数据库迁移失败：$($m.Name)" -ForegroundColor Red; exit 1 }
    }

    Write-Host "[4/4] 启动后端 → http://127.0.0.1:$Port  (Ctrl+C 停止)"
    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
    & $py -m uvicorn app.main:app --host 0.0.0.0 --port $Port
} finally {
    Pop-Location
}
