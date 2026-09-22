"""题库模型：题库来源(bank)、题目、标签。

answer 为多态字段：单选"A"；多选"ABC"；判断"正确/错误"；
填空"a|b"(每空多等价用 / 分隔)；简答长文本；拖拽 {"left":"right"} 映射。
options：单选/多选/判断为 string[]；填空/简答为 []；拖拽用 left_items/right_items。
统一用 TEXT 存 JSON 串。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import PKMixin, TimestampMixin

QUESTION_TYPE = ("单选题", "多选题", "判断题", "填空题", "简答题", "拖拽题")
# 题目未显式设置分值时的默认分。组卷/固化多处需要同一缺省值，收敛到这里避免漂移。
DEFAULT_QUESTION_SCORE = 2.0


class QuestionBank(PKMixin, TimestampMixin):
    __tablename__ = "question_banks"

    name: Mapped[str] = mapped_column(String, nullable=False)
    group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 是否允许用户练习该题库。默认开启；关闭后用户练习入口不再出现该题库，
    # 且「全部题库」范围也会排除它。既有练习记录/错题本不受影响。
    # index=True：enabled_bank_ids 在几乎每个练习/统计请求上都会按该列过滤。
    practice_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1", index=True
    )


class QuestionTag(PKMixin):
    __tablename__ = "question_tags"

    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)


class Question(PKMixin, TimestampMixin):
    __tablename__ = "questions"

    # 题库删除时置空（题目本身保留），避免级联删除题目导致历史成绩失去题目引用
    bank_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("question_banks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String, nullable=False, index=True)  # QUESTION_TYPE
    question: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 选项 / 空位
    left_items: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 拖拽左侧
    right_items: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 拖拽右侧
    # 多态：单选 "A"；多选 "ABC"；判断 "正确"/"错误"；填空 [[...]]；拖拽 {}；简答 str。
    # 列是 NOT NULL，因此注解不应包含 None（否则类型检查通过的构造会在 flush 时 IntegrityError）。
    answer: Mapped[dict | str] = mapped_column(JSON, nullable=False)
    analysis: Mapped[str] = mapped_column(Text, default="", nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, default=2, nullable=False, index=True)  # 1-3
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["甲","乙"]
    score: Mapped[float] = mapped_column(Float, default=DEFAULT_QUESTION_SCORE, nullable=False)
    group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True
    )  # 所属分组，用于授权筛选
