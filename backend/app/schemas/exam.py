"""考试 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationInfo, field_validator


class ExamAnswerIn(BaseModel):
    question_id: int
    answer: Any = None
    version: int = Field(ge=1)


class ExamCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    type: str = Field("formal", pattern="^(mock|formal)$")
    paper_template_id: int | None = None
    manual_questions: list[int] | None = None
    rules: dict = Field(default_factory=dict)
    group_ids: list[int] | None = None
    start_at: str | None = None
    end_at: str | None = None
    duration_min: int = Field(90, ge=1, le=1440)
    pass_score: FiniteFloat = Field(60, ge=0)
    max_attempts: int = Field(0, ge=0)
    show_score_immediately: bool = True
    show_analysis: bool = False
    need_review: bool = False


class ExamUpdateIn(BaseModel):
    """考试更新入参（PATCH 语义：未传字段不修改）。

    注意区分“未传”与“显式传 null”：`exclude_unset=True` 会跳过未传字段，但显式 null
    会被 setattr 写入模型。duration_min / pass_score / max_attempts 在数据库中是 NOT NULL，
    写入 null 会抛出 IntegrityError（500）。这里用 `_reject_explicit_null` 将其拦成 422。
    """

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=200)
    rules: dict | None = None
    group_ids: list[int] | None = None
    start_at: str | None = None
    end_at: str | None = None
    duration_min: int | None = Field(None, ge=1)
    pass_score: FiniteFloat | None = Field(None, ge=0)
    max_attempts: int | None = Field(None, ge=0)
    show_score_immediately: bool | None = None
    show_analysis: bool | None = None
    need_review: bool | None = None
    manual_questions: list[int] | None = None
    paper_template_id: int | None = None

    @field_validator("duration_min", "pass_score", "max_attempts", mode="before")
    @classmethod
    def _reject_explicit_null(cls, v: Any, info: ValidationInfo) -> Any:
        """这些字段对应 NOT NULL 列，拒绝显式 null，避免落库时 500。"""
        if v is None:
            raise ValueError(f"{info.field_name} 不能为 null；如需保持不变请不要提交该字段")
        return v

    @field_validator("rules", mode="before")
    @classmethod
    def _rules_must_be_object(cls, v: Any) -> Any:
        """rules 必须是对象（组卷配置）；拒绝 null/数组等非对象值。"""
        if v is None or not isinstance(v, dict):
            raise ValueError("rules 必须是对象")
        return v


class PaperTemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    mode: str = Field("mock", pattern="^(mock|formal)$")
    config: dict
    group_ids: list[int] | None = None


class PaperPreviewIn(BaseModel):
    """组卷预览入参。

    原实现直接接收裸 `dict`，绕过 `extra="forbid"` 与元素类型校验，
    `bank_ids`/`group_ids`/`tags` 会被原样送入 SQL 的 IN 过滤。此处显式声明类型，
    让非法元素在 422 阶段被拒绝，而不是进入服务层。
    """

    model_config = ConfigDict(extra="forbid")

    type_quota: dict[str, int] = Field(default_factory=dict)
    difficulty_dist: dict[str, float] = Field(default_factory=dict)
    bank_ids: list[int] = Field(default_factory=list)
    group_ids: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    max_questions: int = Field(100, ge=1, le=1000)
    seed: int | None = None
    order_mode: str | None = Field(None, pattern="^(bank|random|grouped)$")

    def to_config(self) -> dict:
        """转为组卷服务所需的配置字典（仅包含显式提供的字段）。"""
        return self.model_dump(exclude_none=True)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: str  # pass/fail/partial
    partial_score: FiniteFloat | None = Field(None, ge=0)


class MockConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict
