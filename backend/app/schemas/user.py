"""用户 schema。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr


class UserListItem(BaseModel):
    id: int
    email: EmailStr
    name: str
    role: str
    status: str
    email_verified: bool
    dept_group_id: Optional[int] = None
    groups: list[int] = []

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    dept_group_id: Optional[int] = None


class Page(BaseModel):
    total: int
    page: int
    page_size: int
    items: list
