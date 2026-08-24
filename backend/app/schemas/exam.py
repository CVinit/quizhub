"""考试 schema。"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ExamAnswerIn(BaseModel):
    question_id: int
    answer: Any = None
    version: int


class ExamCreateIn(BaseModel):
    name: str
    type: str = "formal"  # mock/formal
    paper_template_id: Optional[int] = None
    manual_questions: Optional[list[int]] = None
    rules: dict = {}
    group_ids: Optional[list[int]] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    duration_min: int = 90
    pass_score: float = Field(60, ge=0)
    max_attempts: int = Field(0, ge=0)
    show_score_immediately: bool = True
    show_analysis: bool = False
    need_review: bool = False


class ExamUpdateIn(BaseModel):
    name: Optional[str] = None
    rules: Optional[dict] = None
    group_ids: Optional[list[int]] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    duration_min: Optional[int] = Field(None, ge=1)
    pass_score: Optional[float] = Field(None, ge=0)
    max_attempts: Optional[int] = Field(None, ge=0)
    show_score_immediately: Optional[bool] = None
    show_analysis: Optional[bool] = None
    need_review: Optional[bool] = None
    manual_questions: Optional[list[int]] = None
    paper_template_id: Optional[int] = None


class PaperTemplateIn(BaseModel):
    name: str
    mode: str = "mock"
    config: dict
    group_ids: Optional[list[int]] = None


class ReviewIn(BaseModel):
    verdict: str  # pass/fail/partial
    partial_score: Optional[float] = Field(None, ge=0)


class MockConfigIn(BaseModel):
    config: dict
