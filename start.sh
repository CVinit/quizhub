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

# JWT / 加密密钥：首次运行生成并落盘复用。
# 原实现不加载该文件，导致（1）重启后登录态全部失效；（2）TRAINING_ENC_KEY 变化后
# 已保存的 SMTP 密码无法解密，测试邮件与注册验证码都发不出去。
KEY_FILE="$ROOT/backend/data/.dev-secrets.env"
if [ ! -f "$KEY_FILE" ]; then
  mkdir -p "$(dirname "$KEY_FILE")"
  uv run python -c "import base64,secrets;print('TRAINING_SECRET_KEY='+secrets.token_urlsafe(48));print('TRAINING_ENC_KEY='+base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())" > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  echo "        已生成密钥文件 $KEY_FILE（请勿删除，否则已保存的 SMTP 密码将无法解密）"
fi
set -a
# shellcheck disable=SC1090
. "$KEY_FILE"
set +a

uv run python scripts/init_db.py
# 按文件名顺序执行全部迁移（幂等）；避免新增迁移脚本被遗漏导致缺列
for m in $(ls scripts/migrate_*.py | sort); do
  echo "        执行 $(basename "$m") ..."
  uv run python "$m"
done

echo "[3/3] 启动后端 (http://localhost:8000)..."
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
