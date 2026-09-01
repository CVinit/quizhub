"""认证 Pydantic schema。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _validate_password_bytes(value: str) -> str:
    if len(value.encode("utf-8")) > 72:
        raise ValueError("密码 UTF-8 编码后不能超过 72 字节")
    return value


class SendCodeIn(BaseModel):
    email: EmailStr
    captcha_id: str = Field(min_length=4)
    captcha_code: str = Field(min_length=1, max_length=10)


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=72)
    name: str = Field(default="", max_length=50)
    code: str = Field(min_length=4, max_length=10)
    group_ids: list[int] = Field(default_factory=list)  # 注册时选择的分组（可空，视设置是否必选）

    _password_bytes = field_validator("password")(_validate_password_bytes)


class VerifyIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=10)


class LoginIn(BaseModel):
    username: EmailStr
    password: str


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


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=72)

    _password_bytes = field_validator("new_password")(_validate_password_bytes)


TokenOut.model_rebuild()
