"""分组 schema。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GroupBase(BaseModel):
    name: str = Field(max_length=100)
    type: str = Field(default="自定义")  # 部门/专业/班级/自定义
    parent_id: int | None = None
    sort: int = 0


class GroupCreate(GroupBase):
    pass


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    type: str | None = None
    parent_id: int | None = None
    sort: int | None = None


class GroupOut(GroupBase):
    id: int
    children: list[GroupOut] = []

    class Config:
        from_attributes = True


GroupOut.model_rebuild()


class UserGroupAssign(BaseModel):
    group_ids: list[int] = []
