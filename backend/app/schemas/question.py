"""题目 schema。"""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationInfo, field_validator

from app.core.limits import MAX_ID_LIST_LEN, validate_json_size
from app.schemas._patch import reject_explicit_null


class QuestionBankOut(BaseModel):
    """题库响应。字段与前端 `QuestionBank` 类型一致（原先缺 practice_enabled，
    直接挂到题库接口上会把该字段从响应里抹掉）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    group_id: int | None = None
    practice_enabled: bool = True


class QuestionBankCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
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
    tags: list[str] | None = Field(None, max_length=MAX_ID_LIST_LEN)
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
    tags: list[str] | None = Field(None, max_length=MAX_ID_LIST_LEN)
    score: FiniteFloat = Field(2, ge=0)
    group_id: int | None = None

    @field_validator("options", "left_items", "right_items", "answer")
    @classmethod
    def _limit_json_size(cls, v: Any, info: ValidationInfo) -> Any:
        """限制单个 JSON 字段体积（见 core.limits.MAX_JSON_BYTES）。"""
        return validate_json_size(v, label=str(info.field_name))


class QuestionUpdate(BaseModel):
    """题目更新入参（PATCH 语义：未传字段不修改）。

    questions 的 question / analysis / difficulty / score 是 NOT NULL 列，显式传 null
    会在服务层 setattr + commit 时抛 IntegrityError（500），故由 `_reject_explicit_null`
    拦成 422；可空列（bank_id / options / left_items / right_items / answer / tags /
    group_id）仍允许显式 null。
    """

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
    tags: list[str] | None = Field(None, max_length=MAX_ID_LIST_LEN)
    score: FiniteFloat | None = Field(None, ge=0)
    group_id: int | None = None

    @field_validator("options", "left_items", "right_items", "answer")
    @classmethod
    def _limit_json_size(cls, v: Any, info: ValidationInfo) -> Any:
        """限制单个 JSON 字段体积（见 core.limits.MAX_JSON_BYTES）。"""
        return validate_json_size(v, label=str(info.field_name))

    @field_validator("question", "analysis", "difficulty", "score", mode="before")
    @classmethod
    def _reject_explicit_null(cls, v: Any, info: ValidationInfo) -> Any:
        """这些字段对应 NOT NULL 列，拒绝显式 null，避免落库时 500。"""
        return reject_explicit_null(v, info)


class RowError(TypedDict):
    """Excel 解析错误项（与前端预览表格的列约定一致）。

    定义在 schemas 层供 `utils/excel.py` 复用：utils 依赖 schemas（UploadPreview*），
    反向 import 会成环。
    """

    sheet: str
    row: int
    error: str


class UploadPreviewRow(BaseModel):
    type: str
    question: str
    options: list | None = None
    left_items: list | None = None
    right_items: list | None = None
    answer: Any
    analysis: str = ""
    difficulty: int = 2
    tags: list[str] | None = Field(None, max_length=MAX_ID_LIST_LEN)
    score: FiniteFloat = Field(2, ge=0)
    # 模板「所属分组ID」列：留空表示沿用上传时选择的分组，非空时按行覆盖。
    # 存在性/数据范围/与所选题库分组的一致性由 import_service 校验（utils 层不碰 db）。
    group_id: int | None = None
    row_index: int
    valid: bool = True
    error: str = ""


class UploadPreview(BaseModel):
    rows: list[UploadPreviewRow]
    total: int
    type_dist: dict[str, int]
    errors: list[RowError]
    # 解析阶段触及行数上限、后续数据行被丢弃：由解析器在 break 处**显式**置位。
    # 原实现用 `total >= PARSE_ROW_MAX` 反推截断，前置空行时会静默丢数据且不报截断。
    truncated: bool = False
    # 完整解析结果（内部使用）。`rows` 只是给前端的 20 行预览切片；导入必须用这份
    # 完整数据，避免消费方重新解析同一份字节流而产生"两次解析口径不一致"的缺陷。
    # exclude=True：即使被当作 response_model 也不会出现在响应里（防响应体膨胀）。
    all_rows: list[UploadPreviewRow] = Field(default_factory=list, exclude=True)


class UploadImportResult(BaseModel):
    success: int
    failed: int


class QuestionListOut(BaseModel):
    """题目分页列表响应。

    列表接口此前直接返回 ORM 实例（无 response_model）：字段集随模型演进自动变化，
    与同文件 create/update 受 `QuestionOut` 约束的做法不一致。
    """

    total: int
    page: int
    page_size: int
    items: list[QuestionOut]
