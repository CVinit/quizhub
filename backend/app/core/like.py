"""LIKE 模式转义：把用户输入安全地嵌入 LIKE 模式。

`%` 与 `_` 是 LIKE 的通配符。若不转义，管理员搜索 `_` 会匹配任意单字符、
搜索 `%` 会命中全表，返回与预期无关的结果。此类转义原先只存在于
`audit_service`，其它模块仍是裸拼接 —— 收敛到本模块，杜绝各调用点各持一份实现。

用法（必须同时传 `escape=ESCAPE_CHAR`，否则转义字符本身会被当普通字符）：

    stmt.where(Column.like(like_pattern(keyword), escape=ESCAPE_CHAR))
"""

from __future__ import annotations

ESCAPE_CHAR = "\\"


def like_pattern(raw: str, *, contains: bool = True) -> str:
    """把用户输入转义为安全的 LIKE 模式。

    Args:
        raw: 用户输入的关键词。
        contains: True（默认）时两侧补 `%` 做包含匹配；False 时只转义不加通配符。

    Returns:
        已转义（并按需补两侧 `%`）的 LIKE 模式。

    Examples:
        >>> like_pattern("a_b")
        '%a\\\\_b%'
    """
    escaped = raw.replace(ESCAPE_CHAR, ESCAPE_CHAR * 2).replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%" if contains else escaped
