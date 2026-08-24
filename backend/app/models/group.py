"""分组与用户-分组关联。"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import PKMixin, TimestampMixin

GROUP_TYPE = ("部门", "专业", "班级", "自定义")


class Group(PKMixin, TimestampMixin):
    __tablename__ = "groups"

    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False, default="自定义")  # GROUP_TYPE
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("groups.id"), nullable=True, index=True
    )
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class UserGroup(PKMixin):
    __tablename__ = "user_groups"
    __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_user_group"),)

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("groups.id"), index=True, nullable=False)
