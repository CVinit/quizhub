"""练习与题目状态 schema。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PracticeStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["sequence", "random", "type", "wrong", "mark", "bank"]
    type: str | None = None  # 按题型时传题型名
    limit: int | None = Field(None, ge=1, le=500)
    bank_id: int | None = None  # 限定某题库范围内练习


class PracticeAnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: int
    answer: Any = None
    mode: Literal["sequence", "random", "type", "wrong", "mark", "bank"] = "sequence"


class ToggleMarkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marked: bool
    note: str = Field(default="", max_length=1000)


class ShortEvalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mastered: bool


class QuestionStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    marked: bool
    marked_note: str = ""
