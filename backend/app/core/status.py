"""领域层使用的 HTTP 状态码常量。

`core.errors.DomainError` 的 `status_code` 就是普通 int；服务层此前 `from fastapi import status`
—— 虽然只是常量，却让领域层依赖了传输框架（`core/errors.py` 明确声明「服务层不依赖 FastAPI」）。
这里给出等值常量，服务层/工具层统一从本模块导入；路由层仍可用 FastAPI 的 status（传输层本就在那里）。
"""

from __future__ import annotations

BAD_REQUEST = 400
UNAUTHORIZED = 401
FORBIDDEN = 403
NOT_FOUND = 404
CONFLICT = 409
CONTENT_TOO_LARGE = 413
UNPROCESSABLE_CONTENT = 422
SERVICE_UNAVAILABLE = 503
