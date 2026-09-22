"""练习与题目状态 schema。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.limits import validate_answer_size


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

    @field_validator("answer")
    @classmethod
    def _limit_answer_size(cls, value: Any) -> Any:
        # 与 ExamAnswerIn 共用同一上限：练习记录每次作答新增一行且原样落库，
        # 不限长同样构成写放大/磁盘膨胀面（原先只有考试路径做了限制）。
        return validate_answer_size(value)


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
