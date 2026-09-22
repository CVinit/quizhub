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
