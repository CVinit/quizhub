"""系统设置 schema。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from app.core.limits import MAX_SETTING_KEYS, MAX_SETTING_VALUE_CHARS


class SettingsUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str
    updates: dict[str, str]

    @field_validator("updates")
    @classmethod
    def _limit_updates(cls, value: dict[str, str]) -> dict[str, str]:
        """限制单次提交的设置项数量与单值长度，避免把设置表当存储用。"""
        if len(value) > MAX_SETTING_KEYS:
            raise ValueError(f"单次最多提交 {MAX_SETTING_KEYS} 个设置项")
        for key, item in value.items():
            if len(item) > MAX_SETTING_VALUE_CHARS:
                raise ValueError(f"设置项 {key} 的值不能超过 {MAX_SETTING_VALUE_CHARS} 字符")
        return value


class SmtpTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_email: EmailStr
