"""安全：密码哈希、JWT、验证码、敏感设置加密。"""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.config import ALGORITHM, SECRET_KEY, SETTINGS_ENC_KEY


def hash_password(password: str) -> str:
    pwd = password.encode("utf-8")
    if len(pwd) > 72:
        raise ValueError("密码 UTF-8 编码后不能超过 72 字节")
    return bcrypt.hashpw(pwd, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        pwd = plain.encode("utf-8")
        if len(pwd) > 72:
            return False
        return bcrypt.checkpw(pwd, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str | int, extra: dict[str, Any] | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=7)
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
