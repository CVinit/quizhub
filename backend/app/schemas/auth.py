"""认证 Pydantic schema。"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


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
    id: int
    email: EmailStr
    name: str
    role: str
    status: str
    email_verified: bool

    class Config:
        from_attributes = True


class ResendIn(BaseModel):
    email: EmailStr


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=72)


TokenOut.model_rebuild()
