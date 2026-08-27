"""用户 schema。"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr


class UserListItem(BaseModel):
    id: int
    email: EmailStr
    name: str
    role: str
    status: str
    email_verified: bool
    dept_group_id: int | None = None
    groups: list[int] = []

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    dept_group_id: int | None = None


class Page(BaseModel):
    total: int
    page: int
    page_size: int
    items: list
