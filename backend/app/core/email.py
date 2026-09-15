"""邮箱规范化：全应用唯一的邮箱比较/存储口径。

背景（安全修复）：`EmailStr` 只把 **域名** 转小写，保留 local-part 大小写，
例如 `Admin@Example.com` 会被规范为 `Admin@example.com`。而 `users.email`
在 SQLite 的 BINARY 排序规则下大小写敏感，导致：

1. 用户以 `Admin@x.com` 注册后，用 `admin@x.com` 无法登录（401，与密码错误无法区分）；
2. `admin@x.com` 与 `Admin@x.com` 是两个不同行，均可通过唯一约束，
   可注册视觉上完全相同的重复账号；
3. 限流键已按 `.lower()` 归一，而数据库查询没有，两侧口径不一致。

统一约定：**邮箱一律以 `strip().lower()` 后的形式存储与比较**。
写入路径（注册/管理员建号/导入）与查询路径（登录/找回/去重）都必须
经过本模块，禁止直接比较原始大小写。
"""

from __future__ import annotations


def normalize_email(value: str | None) -> str:
    """把邮箱归一为存储/比较口径：去除首尾空白并整体转小写。

    仅做规范化，不做格式校验；格式校验由 Pydantic `EmailStr` 负责。

    Args:
        value: 原始邮箱字符串，允许为 None。

    Returns:
        规范化后的邮箱；None 或空串返回空字符串。
    """
    if not value:
        return ""
    return value.strip().lower()
