"""安全：密码哈希、JWT、验证码、敏感设置加密。"""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.config import ACCESS_TOKEN_EXPIRE_MINUTES, ALGORITHM, SECRET_KEY, SETTINGS_ENC_KEY

# 敏感设置（如 SMTP 密码）在读取接口中的展示掩码。
# 生产端（API 读取）与消费端（update_settings 跳过写回）必须共用同一常量：
# 若两处字面量不一致，掩码会被当作真实密文写库，永久破坏已保存的凭据。
MASKED_SECRET = "******"

# bcrypt 只使用密码的前 72 字节：超长密码会被静默截断（注册时接受、登录时却对不上）。
# 该规则原先在 schema / service / excel 里各写一份，收敛到此处唯一实现。
MAX_PASSWORD_BYTES = 72


def validate_password_bytes(value: str) -> str:
    """校验密码的 UTF-8 字节长度（bcrypt 上限）。

    Args:
        value: 明文密码。

    Returns:
        原值（便于直接作为 Pydantic field_validator 使用）。

    Raises:
        ValueError: 超过 `MAX_PASSWORD_BYTES`（由 Pydantic 转 422）。
    """
    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"密码 UTF-8 编码后不能超过 {MAX_PASSWORD_BYTES} 字节")
    return value


def hash_password(password: str) -> str:
    pwd = password.encode("utf-8")
    if len(pwd) > MAX_PASSWORD_BYTES:
        raise ValueError(f"密码 UTF-8 编码后不能超过 {MAX_PASSWORD_BYTES} 字节")
    return bcrypt.hashpw(pwd, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        pwd = plain.encode("utf-8")
        if len(pwd) > MAX_PASSWORD_BYTES:
            return False
        return bcrypt.checkpw(pwd, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str | int, extra: dict[str, Any] | None = None) -> str:
    # 有效期统一由 config.ACCESS_TOKEN_EXPIRE_MINUTES 决定（原实现硬编码 days=7，
    # 使该配置项成为永不生效的死配置，两处一旦不同步就是安全/体验问题）。
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(subject), "exp": expire}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def gen_verify_code(length: int = 6) -> str:
    return "".join(secrets.choice(string.digits) for _ in range(length))


def _fernet() -> Any | None:
    if not SETTINGS_ENC_KEY:
        return None
    from cryptography.fernet import Fernet

    return Fernet(SETTINGS_ENC_KEY.encode())


def encrypt_value(value: str) -> str:
    """加密敏感设置值；未配置密钥时拒绝保存非空值。"""
    if not value:
        return ""
    f = _fernet()
    if f is None:
        raise RuntimeError("TRAINING_ENC_KEY 未配置，无法保存敏感设置")
    return "enc:" + f.encrypt(value.encode()).decode()


def decrypt_value(value: str) -> str:
    if not value:
        return ""
    if value.startswith("enc:"):
        f = _fernet()
        if f is None:
            raise RuntimeError("TRAINING_ENC_KEY 未配置，无法读取敏感设置")
        return f.decrypt(value[4:].encode()).decode()
    if value.startswith("plain:"):
        raise RuntimeError("检测到未加密的敏感设置，请配置 TRAINING_ENC_KEY 并迁移数据")
    return value
