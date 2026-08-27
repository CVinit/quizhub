"""考试 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExamAnswerIn(BaseModel):
    question_id: int
    answer: Any = None
    version: int


class ExamCreateIn(BaseModel):
    name: str
    type: str = "formal"  # mock/formal
    paper_template_id: int | None = None
    manual_questions: list[int] | None = None
    rules: dict = {}
    group_ids: list[int] | None = None
    start_at: str | None = None
    end_at: str | None = None
    duration_min: int = 90
    pass_score: float = Field(60, ge=0)
    max_attempts: int = Field(0, ge=0)
    show_score_immediately: bool = True
    show_analysis: bool = False
    need_review: bool = False


class ExamUpdateIn(BaseModel):
    name: str | None = None
    rules: dict | None = None
    group_ids: list[int] | None = None
    start_at: str | None = None
    end_at: str | None = None
    duration_min: int | None = Field(None, ge=1)
    pass_score: float | None = Field(None, ge=0)
    max_attempts: int | None = Field(None, ge=0)
    show_score_immediately: bool | None = None
    show_analysis: bool | None = None
    need_review: bool | None = None
    manual_questions: list[int] | None = None
    paper_template_id: int | None = None


class PaperTemplateIn(BaseModel):
    name: str
    mode: str = "mock"
    config: dict
    group_ids: list[int] | None = None


class ReviewIn(BaseModel):
    verdict: str  # pass/fail/partial
    partial_score: float | None = Field(None, ge=0)


class MockConfigIn(BaseModel):
    config: dict
