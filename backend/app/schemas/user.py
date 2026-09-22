"""用户 schema。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=50)
    role: str | None = None
    dept_group_id: int | None = None
