"""分组 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.core.limits import MAX_ID_LIST_LEN
from app.schemas._patch import reject_explicit_null


class GroupBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    type: str = Field(default="自定义")  # 部门/专业/班级/自定义
    parent_id: int | None = None
    sort: int = 0


class GroupCreate(GroupBase):
    pass


class GroupUpdate(BaseModel):
    """分组更新入参（PATCH 语义：未传字段不修改）。

    groups.name / groups.sort 是 NOT NULL 列，显式传 null 会在 `update_group` 的
    setattr + commit 时抛 IntegrityError（500），故拦成 422；parent_id 为可空列，
    显式 null 表示「移到根」，语义合法，仍允许。
    """

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    type: str | None = None
    parent_id: int | None = None
    sort: int | None = None

    @field_validator("name", "sort", mode="before")
    @classmethod
    def _reject_explicit_null(cls, v: Any, info: ValidationInfo) -> Any:
        """这些字段对应 NOT NULL 列，拒绝显式 null，避免落库时 500。"""
        return reject_explicit_null(v, info)


class GroupOut(GroupBase):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    children: list[GroupOut] = Field(default_factory=list)


GroupOut.model_rebuild()


class UserGroupAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_ids: list[int] = Field(default_factory=list, max_length=MAX_ID_LIST_LEN)
