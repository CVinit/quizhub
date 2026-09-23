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

# JWT / 加密密钥：首次运行生成并落盘复用。
# 原实现不加载该文件，导致（1）重启后登录态全部失效；（2）TRAINING_ENC_KEY 变化后
# 已保存的 SMTP 密码无法解密，测试邮件与注册验证码都发不出去。
$keyFile = Join-Path $PSScriptRoot "backend\data\.dev-secrets.env"
if (-not (Test-Path -LiteralPath $keyFile)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $keyFile) | Out-Null
    $generated = uv run python -c "import base64,secrets;print('TRAINING_SECRET_KEY='+secrets.token_urlsafe(48));print('TRAINING_ENC_KEY='+base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
    Set-Content -LiteralPath $keyFile -Value $generated -Encoding utf8
    Write-Host "        已生成密钥文件 $keyFile（请勿删除，否则已保存的 SMTP 密码将无法解密）"
}
Get-Content -LiteralPath $keyFile | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}

uv run python scripts/init_db.py
if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 数据库初始化失败" -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }
# 按文件名顺序执行全部迁移（幂等）；避免新增迁移脚本被遗漏导致缺列
Get-ChildItem -Path "scripts" -Filter "migrate_*.py" -File | Sort-Object Name | ForEach-Object {
    Write-Host "        执行 $($_.Name) ..."
    uv run python "scripts/$($_.Name)"
    if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 数据库迁移失败：$($_.Name)" -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }
}

Write-Host "[3/3] 启动后端 (http://localhost:8000)..."
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
