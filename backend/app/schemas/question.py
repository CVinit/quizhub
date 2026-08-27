"""题目 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QuestionBankOut(BaseModel):
    id: int
    name: str
    group_id: int | None = None

    class Config:
        from_attributes = True


class QuestionBankCreate(BaseModel):
    name: str = Field(max_length=100)
    group_id: int | None = None


class QuestionOut(BaseModel):
    id: int
    bank_id: int | None = None
    type: str
    question: str
    options: list | None = None
    left_items: list | None = None
    right_items: list | None = None
    answer: Any
    analysis: str = ""
    difficulty: int = 2
    tags: list[str] | None = None
    score: float = Field(2, ge=0)
    group_id: int | None = None

    class Config:
        from_attributes = True


class QuestionCreate(BaseModel):
    bank_id: int | None = None
    type: str
    question: str
    options: list | None = None
    left_items: list | None = None
    right_items: list | None = None
    answer: Any
    analysis: str = ""
    difficulty: int = 2
    tags: list[str] | None = None
    score: float = Field(2, ge=0)
    group_id: int | None = None


class QuestionUpdate(BaseModel):
    bank_id: int | None = None
    type: str | None = None
    question: str | None = None
    options: list | None = None
    left_items: list | None = None
    right_items: list | None = None
    answer: Any | None = None
    analysis: str | None = None
    difficulty: int | None = None
    tags: list[str] | None = None
    score: float | None = Field(None, ge=0)
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
    score: float = Field(2, ge=0)
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
