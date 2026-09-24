"""请求体量上限的单一来源。

单题作答内容以整个 JSON blob 存储（`exam_sessions.answers` / `practice_records.user_answer`）
且每次作答全量重写，不限长会让一次超大答案在后续每次提交时被反复序列化（写放大）。
考试与练习两条路径必须共用同一上限，否则短的那条会被绕过。
"""

from __future__ import annotations

import json
from typing import Any

MAX_ANSWER_BYTES = 16 * 1024

# 单请求可提交的 id / 标签列表上限。这些列表会被原样拼进 SQL 的 `IN (...)`：
# 无上限时单个请求就能撞上 SQLite 的绑定参数上限（`too many SQL variables` → 500），
# 且 `RegisterIn.group_ids` 走公开注册接口（无需登录）。超限由 Pydantic 拦成 422。
MAX_ID_LIST_LEN = 200

# 单个 JSON 字段（组卷 rules、题目 options/left_items/right_items/answer）的体积上限。
# 作答路径已有 16KB 上限，但题库创建/考试规则此前无任何限制：可写入任意大的 JSON blob，
# 造成 SQLite 体积膨胀与后续读取的内存峰值。
MAX_JSON_BYTES = 64 * 1024

# 单条系统设置值的字符上限与单次提交的设置项数量上限（防止把设置表当存储用）
MAX_SETTING_VALUE_CHARS = 4096
MAX_SETTING_KEYS = 50

# 单个**非 multipart** 请求体的体积上限（见 core.body_limit 的 ASGI 中间件）。
# 字段级上限（MAX_ANSWER_BYTES / MAX_JSON_BYTES / password ≤72 等）都在 body 被完整
# 解析进内存**之后**才生效，因此需要一个解析前的兜底上界：否则任意登录用户（甚至未登录
# 的 /api/auth/login）发一个超大 JSON 就能把进程内存打满。
# multipart（上传）不走该阈值：其合法体积由系统设置 upload_max_size_mb 决定，
# 且 core.uploads.read_limited 已做分块累计。
MAX_JSON_REQUEST_BYTES = 1024 * 1024

# 练习作答属于高频核心操作，限流阈值只用于兜底防刷，不能影响正常刷题节奏。
PRACTICE_ANSWER_LIMIT = 600
PRACTICE_ANSWER_WINDOW_SEC = 300


def validate_json_size(value: Any, *, label: str = "内容", limit_bytes: int = MAX_JSON_BYTES) -> Any:
    """校验任意可序列化值的 JSON 体积；超限抛 ValueError（由 Pydantic 转成 422）。

    Args:
        value: 待校验的值（None 直接放行）。
        label: 错误文案中的字段名。
        limit_bytes: 允许的最大字节数。

    Returns:
        原值。

    Raises:
        ValueError: 序列化后超过 `limit_bytes`。
    """
    if value is None:
        return value
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > limit_bytes:
        raise ValueError(f"{label}不能超过 {limit_bytes // 1024}KB")
    return value


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
