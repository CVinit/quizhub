"""题目 schema。"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class QuestionBankOut(BaseModel):
    id: int
    name: str
    group_id: Optional[int] = None

    class Config:
        from_attributes = True


class QuestionBankCreate(BaseModel):
    name: str = Field(max_length=100)
    group_id: Optional[int] = None


class QuestionOut(BaseModel):
    id: int
    bank_id: Optional[int] = None
    type: str
    question: str
    options: Optional[list] = None
    left_items: Optional[list] = None
    right_items: Optional[list] = None
    answer: Any
    analysis: str = ""
    difficulty: int = 2
    tags: Optional[list[str]] = None
    score: float = Field(2, ge=0)
    group_id: Optional[int] = None

    class Config:
        from_attributes = True


class QuestionCreate(BaseModel):
    bank_id: Optional[int] = None
    type: str
    question: str
    options: Optional[list] = None
    left_items: Optional[list] = None
    right_items: Optional[list] = None
    answer: Any
    analysis: str = ""
    difficulty: int = 2
    tags: Optional[list[str]] = None
    score: float = Field(2, ge=0)
    group_id: Optional[int] = None


class QuestionUpdate(BaseModel):
    bank_id: Optional[int] = None
    type: Optional[str] = None
    question: Optional[str] = None
    options: Optional[list] = None
    left_items: Optional[list] = None
    right_items: Optional[list] = None
    answer: Optional[Any] = None
    analysis: Optional[str] = None
    difficulty: Optional[int] = None
    tags: Optional[list[str]] = None
    score: Optional[float] = Field(None, ge=0)
    group_id: Optional[int] = None


class UploadPreviewRow(BaseModel):
    type: str
    question: str
    options: Optional[list] = None
    left_items: Optional[list] = None
    right_items: Optional[list] = None
    answer: Any
    analysis: str = ""
    difficulty: int = 2
    tags: Optional[list[str]] = None
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
