"""统计预聚合表。

每日刷新；排行查询命中预聚合表。
"""

from __future__ import annotations

from sqlalchemy import Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import PKMixin


class StatsUserDaily(PKMixin):
    __tablename__ = "stats_user_daily"
    __table_args__ = (UniqueConstraint("user_id", "date", "group_id", name="uq_user_daily"),)

    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)  # YYYY-MM-DD
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    answer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correct_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wrong_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exam_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exam_score_sum: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    exam_pass_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
