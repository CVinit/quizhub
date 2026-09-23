"""考试 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationInfo, field_validator, model_validator

from app.core.limits import validate_answer_size
from app.core.timeutil import business_tz
from app.schemas._patch import reject_explicit_null


def _parse_exam_time(value: str, field_name: str) -> datetime:
    """解析考试时段字符串为带时区的 datetime。

    无偏移值来自管理端日期选择器（value-format 不带时区），语义是**业务本地时间**，
    与 `exam.common._parse_time` 保持同一口径；无法解析则抛 ValueError（→ 422）。
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{field_name} 必须是 ISO-8601 时间（如 2026-09-18T09:00）") from None
    return parsed.replace(tzinfo=business_tz()) if parsed.tzinfo is None else parsed


def _validated_exam_time(value: str | None, field_name: str | None) -> str | None:
    if value is None or value == "":
        return value
    _parse_exam_time(value, field_name or "时间")
    return value


def _validate_window(start_at: str | None, end_at: str | None) -> None:
    """成对出现时校验先后顺序；单侧由服务层结合库中另一侧校验。"""
    if not start_at or not end_at:
        return
    if _parse_exam_time(end_at, "end_at") <= _parse_exam_time(start_at, "start_at"):
        raise ValueError("考试结束时间必须晚于开始时间")


class ExamAnswerIn(BaseModel):
    question_id: int
    answer: Any = None
    version: int = Field(ge=1)

    @field_validator("answer")
    @classmethod
    def _limit_answer_size(cls, value: Any) -> Any:
        return validate_answer_size(value)


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
    pass_score: FiniteFloat = Field(60, ge=0, le=100)
    max_attempts: int = Field(0, ge=0)
    show_score_immediately: bool = True
    show_analysis: bool = False
    need_review: bool = False

    @field_validator("start_at", "end_at")
    @classmethod
    def _check_time_format(cls, value: str | None, info: ValidationInfo) -> str | None:
        return _validated_exam_time(value, info.field_name)

    @model_validator(mode="after")
    def _check_time_window(self) -> ExamCreateIn:
        _validate_window(self.start_at, self.end_at)
        return self


class ExamUpdateIn(BaseModel):
    """考试更新入参（PATCH 语义：未传字段不修改）。

    注意区分“未传”与“显式传 null”：`exclude_unset=True` 会跳过未传字段，但显式 null
    会被 setattr 写入模型。凡是 exam_definitions 中的 NOT NULL 列（name / duration_min /
    pass_score / max_attempts / show_score_immediately / show_analysis / need_review），
    写入 null 都会抛 IntegrityError（500）。这里用 `_reject_explicit_null` 将其拦成 422。

    可空列（rules / group_ids / start_at / end_at / manual_questions / paper_template_id）
    仍允许显式 null。
    """

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=200)
    rules: dict | None = None
    group_ids: list[int] | None = None
    start_at: str | None = None
    end_at: str | None = None
    duration_min: int | None = Field(None, ge=1, le=1440)
    pass_score: FiniteFloat | None = Field(None, ge=0, le=100)
    max_attempts: int | None = Field(None, ge=0)
    show_score_immediately: bool | None = None
    show_analysis: bool | None = None
    need_review: bool | None = None
    manual_questions: list[int] | None = None
    paper_template_id: int | None = None
    # 组卷来源（rules/manual_questions/paper_template_id）变更且该考试已有作答时，
    # 必须显式传 true 才会作废旧作答并重新固化；否则服务端返回 409 影响面提示。
    confirm_reset: bool = False

    @field_validator(
        "name",
        "duration_min",
        "pass_score",
        "max_attempts",
        "show_score_immediately",
        "show_analysis",
        "need_review",
        mode="before",
    )
    @classmethod
    def _reject_explicit_null(cls, v: Any, info: ValidationInfo) -> Any:
        """这些字段对应 NOT NULL 列，拒绝显式 null，避免落库时 500。"""
        return reject_explicit_null(v, info)

    @field_validator("rules", mode="before")
    @classmethod
    def _rules_must_be_object(cls, v: Any) -> Any:
        """rules 必须是对象（组卷配置）；拒绝 null/数组等非对象值。"""
        if v is None or not isinstance(v, dict):
            raise ValueError("rules 必须是对象")
        return v

    @field_validator("start_at", "end_at")
    @classmethod
    def _check_time_format(cls, value: str | None, info: ValidationInfo) -> str | None:
        return _validated_exam_time(value, info.field_name)

    @model_validator(mode="after")
    def _check_time_window(self) -> ExamUpdateIn:
        # 只校验同时提交的两侧；单侧更新由 exam/admin._validate_exam_window 结合库中值校验
        _validate_window(self.start_at, self.end_at)
        return self


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


class MockPaperIn(BaseModel):
    """模拟考试组卷设置（预览与开考共用）。

    模拟考试为完全用户自助：题库范围、题量、题型比例均由用户开考前指定，
    后台不再提供模拟考试配置。所有字段可选并带默认值，缺省即"全库 / 30 题 / 自动分配"。
    """

    model_config = ConfigDict(extra="forbid")

    bank_ids: list[int] | None = None
    size: int | None = Field(None, ge=1)
    type_quota: dict[str, int] | None = None
    allocation: str = Field("auto", pattern="^(auto|manual)$")
    objective_only: bool = False

    @field_validator("type_quota")
    @classmethod
    def _check_quota(cls, v: dict[str, int] | None) -> dict[str, int] | None:
        """题型数量必须是非负整数；题型名白名单在服务层校验（与 QUESTION_TYPES 同源）。"""
        if v is None:
            return None
        for key, num in v.items():
            if isinstance(num, bool) or not isinstance(num, int) or num < 0:
                raise ValueError(f"题型数量必须是非负整数：{key}")
        return v

    @field_validator("bank_ids")
    @classmethod
    def _check_bank_ids(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return None
        for bid in v:
            if isinstance(bid, bool) or not isinstance(bid, int) or bid <= 0:
                raise ValueError("题库 id 必须是正整数")
        return v


class MockStartIn(MockPaperIn):
    """模拟考试开考入参：在组卷设置基础上增加交卷后是否回显解析。"""

    show_analysis: bool = True


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["pass", "fail", "partial"]
    partial_score: FiniteFloat | None = Field(None, ge=0)
