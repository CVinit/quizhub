"""统计预聚合表。

每日刷新；排行查询命中预聚合表。
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import PKMixin


class StatsUserDaily(PKMixin):
    __tablename__ = "stats_user_daily"
    __table_args__ = (
        UniqueConstraint("user_id", "date", "group_id", name="uq_user_daily"),
        # 排行查询按 date 范围 + user_id 分组，唯一约束的前缀无法服务该访问模式
        Index("ix_stats_date_user", "date", "user_id"),
    )

    # 删除用户时级联清理其聚合行，避免排行出现 "已删除用户 #<id>" 的幽灵条目
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)  # YYYY-MM-DD
    group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    answer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correct_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wrong_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exam_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exam_score_sum: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    exam_pass_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
