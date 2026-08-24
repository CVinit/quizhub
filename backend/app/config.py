"""应用配置（环境变量层）。

由运维经环境变量注入的少量密钥类配置；业务配置见 settings 表。
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "training.db"
FILES_DIR = DATA_DIR / "files"


def _secret_key() -> str:
    """JWT 签名密钥。

    生产环境必须经 TRAINING_SECRET_KEY 注入。未设置时生成进程级随机密钥
    （重启后旧 token 失效），杜绝使用已提交到仓库的可猜测默认值伪造 token。
    """
    key = os.getenv("TRAINING_SECRET_KEY", "")
    if key:
        return key
    print("[security][WARN] TRAINING_SECRET_KEY 未设置，已生成进程级临时密钥；"
          "重启后所有登录将失效。生产环境务必经环境变量注入固定密钥。")
    return secrets.token_urlsafe(48)


# JWT 密钥：生产环境务必经 env 注入
SECRET_KEY = _secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 天

# Fernet 密钥（用于加密 SMTP 密码等敏感设置）；32 url-safe base64 字节
SETTINGS_ENC_KEY = os.getenv("TRAINING_ENC_KEY", "")
if not SETTINGS_ENC_KEY:
    print("[security][WARN] TRAINING_ENC_KEY 未设置，敏感设置仅以 base64 可逆编码存储。"
          "生产环境务必经环境变量注入 Fernet 密钥。")

# 超管初始化账号（init_db.py 使用）
SUPER_ADMIN_EMAIL = os.getenv("TRAINING_SUPER_ADMIN_EMAIL", "admin@example.com")
SUPER_ADMIN_PASSWORD = os.getenv("TRAINING_SUPER_ADMIN_PASSWORD", "admin12345")


DATA_DIR.mkdir(parents=True, exist_ok=True)
FILES_DIR.mkdir(parents=True, exist_ok=True)
