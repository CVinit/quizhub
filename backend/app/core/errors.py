"""领域错误：让服务层不依赖 FastAPI 的异常类型。

服务层此前直接 `raise fastapi.HTTPException(...)`，把领域/应用逻辑与 HTTP 传输层耦合：
- 服务无法脱离 FastAPI 复用，测试也必须导入 Web 框架；
- "失败的业务语义" 与 "返回哪个 HTTP 状态码" 混为一体。

这里提供与 `HTTPException` 同形状（`status_code` / `detail`）的领域错误。
`app.main` 注册的异常处理器把它统一映射为 `{"detail": ...}` 的 JSON 响应，
因此路由层与服务层都无需感知传输实现。
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """领域错误基类。

    Attributes:
        status_code: 建议的 HTTP 状态码，由传输层处理器使用。
        detail: 面向调用方的错误说明（字符串或结构化 dict）。
    """

    def __init__(self, status_code: int, detail: Any = "") -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


def is_unique_violation(error: BaseException, *, column: str) -> bool:
    """判断一个 IntegrityError 是否由 `column` 的唯一约束引起。

    并发建号/注册时，「邮箱唯一约束」与其它约束（外键等）抛的都是 IntegrityError，而
    SQLAlchemy 给的消息是通用的。若不加区分地统一归因（例如一律报「该邮箱已存在」），
    会把排查方向引偏 —— 明明是「分组不存在」，管理员却去查邮箱。

    实现上不依赖 SQLAlchemy：驱动原文形如 `UNIQUE constraint failed: users.email`，
    按列名出现即可判定（`orig` 是 SQLAlchemy 包装的原生异常，取不到时退回异常本身）。

    Args:
        error: 捕获到的异常（通常是 `sqlalchemy.exc.IntegrityError`）。
        column: 形如 `users.email` 的「表.列」限定名。

    Returns:
        是否命中该列的唯一约束。
    """
    return f"UNIQUE constraint failed: {column}" in str(getattr(error, "orig", error))
