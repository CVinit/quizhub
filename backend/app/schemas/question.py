"""题目 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat


class QuestionBankOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    group_id: int | None = None


class QuestionBankCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=100)
    group_id: int | None = None
    practice_enabled: bool = True


class QuestionBankUpdate(BaseModel):
    """题库更新：仅允许改名与练习开关（PATCH 语义，未传字段不变）。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=100)
    practice_enabled: bool | None = None


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bank_id: int | None = None
    type: str
    question: str = Field(min_length=1, max_length=10000)
    options: list | None = None
    left_items: list | None = None
    right_items: list | None = None
    answer: Any
    analysis: str = ""
    difficulty: int = Field(2, ge=1, le=3)
    tags: list[str] | None = None
    score: FiniteFloat = Field(2, ge=0)
    group_id: int | None = None


class QuestionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bank_id: int | None = None
    type: str
    question: str = Field(min_length=1, max_length=10000)
    options: list | None = None
    left_items: list | None = None
    right_items: list | None = None
    answer: Any
    analysis: str = ""
    difficulty: int = Field(2, ge=1, le=3)
    tags: list[str] | None = None
    score: FiniteFloat = Field(2, ge=0)
    group_id: int | None = None


class QuestionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bank_id: int | None = None
    type: str | None = None
    question: str | None = Field(None, min_length=1, max_length=10000)
    options: list | None = None
    left_items: list | None = None
    right_items: list | None = None
    answer: Any | None = None
    analysis: str | None = None
    difficulty: int | None = Field(None, ge=1, le=3)
    tags: list[str] | None = None
    score: FiniteFloat | None = Field(None, ge=0)
    group_id: int | None = None


class UploadPreviewRow(BaseModel):
    type: str
    question: str
    options: list | None = None
    left_items: list | None = None
    right_items: list | None = None
    answer: Any
    analysis: str = ""
    difficulty: int = 2
    tags: list[str] | None = None
    score: FiniteFloat = Field(2, ge=0)
    row_index: int
    valid: bool = True
    error: str = ""


class UploadPreview(BaseModel):
    rows: list[UploadPreviewRow]
    total: int
    type_dist: dict[str, int]
    errors: list[dict]


class UploadImportResult(BaseModel):
    success: int
    failed: int
