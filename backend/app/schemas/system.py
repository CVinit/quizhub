"""系统设置 schema。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr


class SettingsUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str
    updates: dict[str, str]


class SmtpTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_email: EmailStr
