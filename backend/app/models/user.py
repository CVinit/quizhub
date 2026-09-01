"""用户与邮箱验证码模型。"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import PKMixin, TimestampMixin

USER_ROLE = ("user", "dept_admin", "super_admin")
USER_STATUS = ("active", "pending", "disabled")


class User(PKMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, default="")
    role: Mapped[str] = mapped_column(String, default="user", nullable=False)  # USER_ROLE
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)  # USER_STATUS
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dept_group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("groups.id", use_alter=True), nullable=True
    )  # 部门管理员的归属部门；普通用户主要分组见 user_groups


class EmailVerification(PKMixin):
    __tablename__ = "email_verifications"

    email: Mapped[str] = mapped_column(String, index=True, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    expire_at: Mapped[str] = mapped_column(String, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
