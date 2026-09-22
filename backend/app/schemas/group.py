"""分组 schema。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GroupBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    type: str = Field(default="自定义")  # 部门/专业/班级/自定义
    parent_id: int | None = None
    sort: int = 0


class GroupCreate(GroupBase):
    pass


class GroupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    type: str | None = None
    parent_id: int | None = None
    sort: int | None = None


class GroupOut(GroupBase):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    children: list[GroupOut] = Field(default_factory=list)


GroupOut.model_rebuild()


class UserGroupAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_ids: list[int] = Field(default_factory=list)
