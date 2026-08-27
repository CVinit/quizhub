"""题库模型：题库来源(bank)、题目、标签。

answer 为多态字段：单选"A"；多选"ABC"；判断"正确/错误"；
填空"a|b"(每空多等价用 / 分隔)；简答长文本；拖拽 {"left":"right"} 映射。
options：单选/多选/判断为 string[]；填空/简答为 []；拖拽用 left_items/right_items。
统一用 TEXT 存 JSON 串。
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import PKMixin, TimestampMixin

QUESTION_TYPE = ("单选题", "多选题", "判断题", "填空题", "简答题", "拖拽题")


class QuestionBank(PKMixin, TimestampMixin):
    __tablename__ = "question_banks"

    name: Mapped[str] = mapped_column(String, nullable=False)
    group_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("groups.id"), nullable=True, index=True)


class QuestionTag(PKMixin):
    __tablename__ = "question_tags"

    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)


class Question(PKMixin, TimestampMixin):
    __tablename__ = "questions"

    bank_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("question_banks.id"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String, nullable=False, index=True)  # QUESTION_TYPE
    question: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 选项 / 空位
    left_items: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 拖拽左侧
    right_items: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 拖拽右侧
    answer: Mapped[dict | str | None] = mapped_column(JSON, nullable=False)  # 多态
    analysis: Mapped[str] = mapped_column(Text, default="", nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, default=2, nullable=False)  # 1-3
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["甲","乙"]
    score: Mapped[float] = mapped_column(Float, default=2, nullable=False)
    group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("groups.id"), nullable=True, index=True
    )  # 所属分组，用于授权筛选
