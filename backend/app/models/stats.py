"""统计预聚合表（每日刷新）。

当前唯一消费方是管理端概览的「今日活跃」：只按 (user_id, date) 计数，行存在即代表当日活跃。
2026-09-23 排行榜下线后，考试类聚合列（exam_count / exam_score_sum / exam_pass_count）
已一并移除——它们不再有读取方，考试只参与「当日是否活跃」的判定（见 stats/aggregate.py）。

注：answer_count / correct_count / wrong_count 目前同样没有读取方，保留用于活动标记与后续
报表；若确认长期不用，可连同聚合逻辑一起下线（见 docs/backend_review_2026-09-23.md 3.2）。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import PKMixin


class StatsUserDaily(PKMixin):
    __tablename__ = "stats_user_daily"
    __table_args__ = (
        UniqueConstraint("user_id", "date", "group_id", name="uq_user_daily"),
        # NULL 在唯一约束中互不相等（SQLite 与标准 SQL 均如此），因此 group_id 为 NULL 的
        # 「未分组」行不受上面的约束保护：重复刷新可能插出两行，按用户聚合的 SUM 会重复计分。
        # 用部分唯一索引补齐该不变式（存量去重见 scripts/migrate_2026_09_18.py）。
        Index(
            "uq_user_daily_ungrouped",
            "user_id",
            "date",
            unique=True,
            sqlite_where=text("group_id IS NULL"),
        ),
        # 按日期维度查询（管理端概览的「今日活跃」）需要 date 前缀，唯一约束的前缀无法服务该访问模式
        Index("ix_stats_date_user", "date", "user_id"),
    )

    # 删除用户时级联清理其聚合行，避免排行出现 "已删除用户 #<id>" 的幽灵条目
    # 注：user_id / date 上的单列索引已被 uq_user_daily（user_id 前缀）与
    # ix_stats_date_user（date 前缀）覆盖，不再单独建索引以免写放大。
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)  # YYYY-MM-DD
    group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    answer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correct_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wrong_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
