"""分组 schema。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class GroupBase(BaseModel):
    name: str = Field(max_length=100)
    type: str = Field(default="自定义")  # 部门/专业/班级/自定义
    parent_id: Optional[int] = None
    sort: int = 0


class GroupCreate(GroupBase):
    pass


class GroupUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=100)
    type: Optional[str] = None
    parent_id: Optional[int] = None
    sort: Optional[int] = None


class GroupOut(GroupBase):
    id: int
    children: list["GroupOut"] = []

    class Config:
        from_attributes = True


GroupOut.model_rebuild()


class UserGroupAssign(BaseModel):
    group_ids: list[int] = []
