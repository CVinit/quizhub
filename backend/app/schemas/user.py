"""用户 schema。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    name: str
    role: str
    status: str
    email_verified: bool
    dept_group_id: int | None = None
    groups: list[int] = []


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=50)
    role: str | None = None
    dept_group_id: int | None = None


class Page(BaseModel):
    total: int
    page: int
    page_size: int
    items: list
