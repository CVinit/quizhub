"""系统设置、审计日志、草稿。"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import PKMixin, TimestampMixin


class Setting(PKMixin):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("key", name="uq_setting_key"),)

    setting_key: Mapped[str] = mapped_column("key", String, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String, default="general", nullable=False, index=True)
    encrypted: Mapped[bool] = mapped_column(default=False, nullable=False)  # type: ignore[arg-type]


class AuditLog(PKMixin, TimestampMixin):
    __tablename__ = "audit_logs"
    # 审计表只增不减，而管理端按 created_at 范围过滤 + count，不建索引会退化为全表扫描
    __table_args__ = (Index("ix_audit_logs_created_at", "created_at"),)

    # 审计日志需长期留存以追溯：删除操作者时置空而非级联删除（保留操作痕迹）
    actor: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String, nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String, default="", nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String, default="", nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str] = mapped_column(String, default="", nullable=False)


class Draft(PKMixin):
    __tablename__ = "drafts"
    __table_args__ = (UniqueConstraint("user_id", "form_key", name="uq_user_draft"),)

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    form_key: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
