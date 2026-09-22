"""请求体量上限的单一来源。

单题作答内容以整个 JSON blob 存储（`exam_sessions.answers` / `practice_records.user_answer`）
且每次作答全量重写，不限长会让一次超大答案在后续每次提交时被反复序列化（写放大）。
考试与练习两条路径必须共用同一上限，否则短的那条会被绕过。
"""

from __future__ import annotations

import json
from typing import Any

MAX_ANSWER_BYTES = 16 * 1024

# 练习作答属于高频核心操作，限流阈值只用于兜底防刷，不能影响正常刷题节奏。
PRACTICE_ANSWER_LIMIT = 600
PRACTICE_ANSWER_WINDOW_SEC = 300


def validate_answer_size(value: Any) -> Any:
    """校验作答内容的 JSON 体积；超限抛 ValueError（由 Pydantic 转成 422）。

    Args:
        value: 作答内容（任意可 JSON 序列化的结构）。

    Returns:
        原值（便于直接作为 field_validator 使用）。

    Raises:
        ValueError: 序列化后超过 `MAX_ANSWER_BYTES`。
    """
    if value is None:
        return value
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_ANSWER_BYTES:
        raise ValueError(f"单题作答内容不能超过 {MAX_ANSWER_BYTES // 1024}KB")
    return value
