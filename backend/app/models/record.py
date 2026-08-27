"""作答与进度模型。

practice_records：练习逐题记录。
question_states：题目状态（合并 practiced/correct/wrong/标记/掌握）。
exam_sessions：考试会话（乐观锁 version），answers 承载逐题作答。
exam_results：结算成绩（含简答复核 published 流程）。
short_answer_reviews：简答人工复核。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import PKMixin, TimestampMixin

PRACTICE_MODE = ("sequence", "random", "type", "wrong", "mark")
QUESTION_STATUS = ("unanswered", "correct", "wrong", "mastered")
SESSION_STATUS = ("in_progress", "submitted", "scoring", "scored", "reviewed")
REVIEW_VERDICT = ("pass", "fail", "partial")


class PracticeRecord(PKMixin):
    __tablename__ = "practice_records"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("questions.id"), index=True, nullable=False)
    bank_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[str] = mapped_column(String, default="sequence", nullable=False)  # PRACTICE_MODE
    user_answer: Mapped[dict | str | None] = mapped_column(JSON, nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # 简答为 None 直到自评
    self_eval: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    answered_at: Mapped[str] = mapped_column(String, nullable=False, index=True)


class QuestionState(PKMixin):
    __tablename__ = "question_states"
    __table_args__ = (UniqueConstraint("user_id", "question_id", name="uq_user_question_state"),)

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("questions.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String, default="unanswered", nullable=False)  # QUESTION_STATUS
    marked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    marked_note: Mapped[str] = mapped_column(String, default="", nullable=False)
    answered_at: Mapped[str | None] = mapped_column(String, nullable=True)


class ExamSession(PKMixin, TimestampMixin):
    __tablename__ = "exam_sessions"

    exam_definition_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_definitions.id"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String, default="in_progress", nullable=False, index=True)  # SESSION_STATUS
    answers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # {qId:{answer,seq,answered_at}}
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    submitted_at: Mapped[str | None] = mapped_column(String, nullable=True)
    remaining_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ExamResult(PKMixin, TimestampMixin):
    __tablename__ = "exam_results"

    exam_definition_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_definitions.id"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    exam_session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("exam_sessions.id"), nullable=True, index=True
    )
    score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, default=100, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    correct_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    objective_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    need_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)


class ShortAnswerReview(PKMixin):
    __tablename__ = "short_answer_reviews"

    exam_result_id: Mapped[int] = mapped_column(Integer, ForeignKey("exam_results.id"), index=True, nullable=False)
    exam_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("questions.id"), nullable=False)
    user_answer: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reference_answer: Mapped[str] = mapped_column(Text, default="", nullable=False)
    verdict: Mapped[str | None] = mapped_column(String, nullable=True)  # REVIEW_VERDICT
    partial_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviewer: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[str | None] = mapped_column(String, nullable=True)
