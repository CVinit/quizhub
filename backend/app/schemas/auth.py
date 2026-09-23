"""认证 Pydantic schema。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.email import normalize_email
from app.core.limits import MAX_ID_LIST_LEN
from app.core.security import validate_password_bytes


class SendCodeIn(BaseModel):
    email: EmailStr
    # 其余凭据字段都有上限，captcha_id 也必须有：它是客户端提交的字符串，
    # 无上限会让超长值进入内存字典的键查找路径。
    captcha_id: str = Field(min_length=4, max_length=64)
    captcha_code: str = Field(min_length=1, max_length=10)

    _norm_email = field_validator("email")(normalize_email)


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=72)
    name: str = Field(default="", max_length=50)
    code: str = Field(min_length=4, max_length=10)
    # 上限见 core/limits：公开接口的 list 入参会直达 SQL 的 IN (...)
    group_ids: list[int] = Field(default_factory=list, max_length=MAX_ID_LIST_LEN)  # 注册分组（可空）

    _norm_email = field_validator("email")(normalize_email)
    _password_bytes = field_validator("password")(validate_password_bytes)


class VerifyIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=10)

    _norm_email = field_validator("email")(normalize_email)


class LoginIn(BaseModel):
    username: EmailStr
    # 上限 72 字节与 bcrypt 一致：避免超大请求体在解析阶段占用内存
    password: str = Field(max_length=72)

    _norm_email = field_validator("username")(normalize_email)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    name: str
    role: str
    status: str
    email_verified: bool


class ResendIn(BaseModel):
    email: EmailStr

    _norm_email = field_validator("email")(normalize_email)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(max_length=72)
    new_password: str = Field(min_length=6, max_length=72)

    _password_bytes = field_validator("new_password")(validate_password_bytes)


TokenOut.model_rebuild()
