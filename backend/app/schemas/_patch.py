"""PATCH 语义的共用校验：拒绝把「显式 null」写进 NOT NULL 列。

`model_dump(exclude_unset=True)` 能区分「未传」与「显式传 null」，但服务层是
`setattr(model, key, value)` 直写：显式 null 会在 commit 时抛 IntegrityError，
而 `main.py` 只注册了 DomainError 处理器，最终表现为 500。

各 Update schema 用本函数把这些字段拦成 422，同时保留「未传 = 不修改」的 PATCH 语义。
可空列（如 exam 的 start_at / group_ids、question 的 bank_id / group_id）不受影响，
仍允许显式 null。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationInfo


def reject_explicit_null(value: Any, info: ValidationInfo) -> Any:
    """拒绝显式 null，保留「未传字段」的 PATCH 语义。

    Args:
        value: 字段值（`mode="before"` 校验器入参，可能是 None）。
        info: Pydantic 校验上下文，用于取字段名。

    Returns:
        原值（非 None 时）。

    Raises:
        ValueError: 字段被显式提交为 null（由 Pydantic 转成 422）。
    """
    if value is None:
        raise ValueError(f"{info.field_name} 不能为 null；如需保持不变请不要提交该字段")
    return value
