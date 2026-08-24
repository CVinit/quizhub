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
    pwd = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pwd, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
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


def _fernet() -> "Any | None":
    if not SETTINGS_ENC_KEY:
        return None
    from cryptography.fernet import Fernet
    return Fernet(SETTINGS_ENC_KEY.encode())


def encrypt_value(value: str) -> str:
    """加密敏感设置值；未配置 ENC_KEY 时回退为 base64 占位（开发期）。"""
    f = _fernet()
    if f is None:
        import base64
        return "plain:" + base64.b64encode(value.encode()).decode()
    return "enc:" + f.encrypt(value.encode()).decode()


def decrypt_value(value: str) -> str:
    if not value:
        return ""
    if value.startswith("enc:"):
        f = _fernet()
        if f is None:
            return ""
        return f.decrypt(value[4:].encode()).decode()
    if value.startswith("plain:"):
        import base64
        return base64.b64decode(value[6:].encode()).decode()
    return value
