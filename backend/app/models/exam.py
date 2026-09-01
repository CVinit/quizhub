"""考试与试卷模板。

paper_templates.config：规则组卷配置（题型/难度配比、来源筛选、种子等）。
exam_definitions.rules：单场考试规则快照（含开放时段、限时、及格线、尝试次数等）。
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import PKMixin, TimestampMixin

EXAM_TYPE = ("mock", "formal")
EXAM_STATUS = ("draft", "published", "ongoing", "ended", "reviewing")


class PaperTemplate(PKMixin, TimestampMixin):
    __tablename__ = "paper_templates"

    name: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, default="mock", nullable=False)  # EXAM_TYPE
    config: Mapped[dict] = mapped_column(JSON, nullable=False)  # 组卷规则
    group_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    question_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 固化题目清单
    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)


class ExamDefinition(PKMixin, TimestampMixin):
    __tablename__ = "exam_definitions"

    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, default="formal", nullable=False, index=True)  # EXAM_TYPE
    paper_template_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("paper_templates.id"), nullable=True)
    manual_questions: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 手选题 id 列表
    rules: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    group_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 指派分组
    start_at: Mapped[str | None] = mapped_column(String, nullable=True)
    end_at: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_min: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    pass_score: Mapped[float] = mapped_column(Float, default=60, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0=不限
    show_score_immediately: Mapped[bool] = mapped_column(default=True, nullable=False)  # type: ignore[arg-type]
    show_analysis: Mapped[bool] = mapped_column(default=False, nullable=False)  # type: ignore[arg-type]
    need_review: Mapped[bool] = mapped_column(default=False, nullable=False)  # type: ignore[arg-type]
    status: Mapped[str] = mapped_column(String, default="draft", nullable=False, index=True)
    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)


class ExamQuestion(PKMixin):
    __tablename__ = "exam_questions"
    # 唯一约束：防止首次固化题目的 check-then-insert 竞态产生重复行（同一考试同一题不重复计分）
    __table_args__ = (UniqueConstraint("exam_definition_id", "question_id", name="uq_exam_question"),)

    exam_definition_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_definitions.id"), index=True, nullable=False
    )
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("questions.id"), index=True, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    score: Mapped[float] = mapped_column(Float, default=2, nullable=False)
    shuffle_map: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 选项打乱映射
