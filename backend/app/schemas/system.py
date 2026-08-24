"""系统设置 schema。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr


class SettingsUpdateIn(BaseModel):
    category: str
    updates: dict[str, str]


class SmtpTestIn(BaseModel):
    to_email: EmailStr
