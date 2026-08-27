"""练习与题目状态 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PracticeStartIn(BaseModel):
    mode: str  # sequence/random/type/wrong/mark/bank
    type: str | None = None  # 按题型时传题型名
    limit: int | None = Field(None, ge=1, le=500)
    bank_id: int | None = None  # 限定某题库范围内练习


class PracticeAnswerIn(BaseModel):
    question_id: int
    answer: Any = None
    mode: str = "sequence"


class ToggleMarkIn(BaseModel):
    marked: bool
    note: str = ""


class ShortEvalIn(BaseModel):
    mastered: bool


class QuestionStateOut(BaseModel):
    status: str
    marked: bool
    marked_note: str = ""

    class Config:
        from_attributes = True
