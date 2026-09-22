"""用户与邮箱验证码模型。"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import PKMixin, TimestampMixin

USER_ROLE = ("user", "dept_admin", "super_admin")
USER_STATUS = ("active", "pending", "disabled")


class User(PKMixin, TimestampMixin):
    __tablename__ = "users"

    # NOCASE：邮箱以 normalize_email() 归一后存储，此排序规则作为二次防线，
    # 保证即使有历史大小写混写的旧数据，唯一约束与等值查询仍按不区分大小写生效。
    email: Mapped[str] = mapped_column(String(collation="NOCASE"), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, default="")
    role: Mapped[str] = mapped_column(String, default="user", nullable=False, index=True)  # USER_ROLE
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False, index=True)  # USER_STATUS
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # index=True：部门数据范围过滤（core.deps.user_ids_subquery）几乎出现在每个
    # 部门管理员查询里，而 SQLite 不会为外键自动建索引。
    dept_group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="SET NULL", use_alter=True), nullable=True, index=True
    )  # 部门管理员的归属部门；普通用户主要分组见 user_groups


class EmailVerification(PKMixin):
    __tablename__ = "email_verifications"

    email: Mapped[str] = mapped_column(String, index=True, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    expire_at: Mapped[str] = mapped_column(String, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
