"""用户 schema。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.limits import MAX_ID_LIST_LEN
from app.core.security import validate_password_bytes
from app.models.user import ROLE_USER, STATUS_ACTIVE


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=50)
    role: str | None = None
    dept_group_id: int | None = None


class ResetPasswordIn(BaseModel):
    """管理员重置密码入参（原先定义在 api/users.py，与其它请求模型分层不一致）。"""

    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(min_length=6, max_length=72)

    _password_bytes = field_validator("new_password")(validate_password_bytes)


class UserCreateIn(BaseModel):
    """管理员新增用户入参。"""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    name: str = Field(default="", max_length=50)
    role: str = Field(default=ROLE_USER)
    password: str = Field(min_length=6, max_length=72)
    status: str = Field(default=STATUS_ACTIVE)
    group_ids: list[int] = Field(default_factory=list, max_length=MAX_ID_LIST_LEN)

    _password_bytes = field_validator("password")(validate_password_bytes)
