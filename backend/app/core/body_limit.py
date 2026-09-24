"""请求体体积上限（ASGI 中间件）：非 multipart 请求的解析前兜底。

**为什么需要**：JSON 请求体会被 Starlette 的 `Request.json()` 完整读进内存，之后才轮到
Pydantic 的字段级上限（`MAX_ANSWER_BYTES` / `MAX_JSON_BYTES` / `password ≤72`）生效。
上传路径已有「分块读取 + 累计上限」（`core.uploads.read_limited`），草稿接口也自查了
`Content-Length`，但其余 JSON 端点（含未登录可达的 `/api/auth/login`）此前没有任何上界：
一个超大 body 就能把进程内存打满。

**设计取舍**：

- 只约束**非 multipart** 请求。multipart 的合法体积由系统设置 `upload_max_size_mb` 决定，
  全局阈值放这里会与可配置上限打架；其内存安全由 `read_limited` 的分块累计保证。
- `Content-Length` 声明超限 → 立刻 413，连 body 都不读。
- 声明缺失或撒谎（chunked、伪造小值）→ 边收边计数，超限立即中断读取。

注册位置见 `app.main.create_app`：它被 CORS 包在里面，因此 413 响应同样带跨域头。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.limits import MAX_JSON_REQUEST_BYTES

logger = logging.getLogger("quizhub")


class BodyTooLarge(Exception):
    """请求体超过上限；由本中间件自己捕获并转成 413。"""


def _header(scope: dict, name: bytes) -> bytes | None:
    for key, value in scope.get("headers") or ():
        if key == name:
            return value
    return None


class BodySizeLimitMiddleware:
    """给非 multipart 请求体加上限的 ASGI 中间件。"""

    def __init__(self, app: Any, max_bytes: int = MAX_JSON_REQUEST_BYTES) -> None:
        """初始化中间件。

        Args:
            app: 下游 ASGI 应用。
            max_bytes: 允许的最大请求体字节数。
        """
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or (scope.get("method") or "").upper() in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        content_type = _header(scope, b"content-type") or b""
        if content_type.lower().startswith(b"multipart/"):
            await self.app(scope, receive, send)
            return

        declared = _header(scope, b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, send)
            return

        received = 0
        started = False

        async def _limited_receive() -> dict:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge
            return message

        async def _tracking_send(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, _limited_receive, _tracking_send)
        except BodyTooLarge:
            # 路由在读完 body 之前不会开始响应，因此正常情况下这里补发 413 是安全的；
            # 万一响应已开始（不应发生），只留日志，避免发出第二个响应头。
            if started:
                logger.warning("[api] 请求体超限但响应已开始，无法回 413 path=%s", scope.get("path"))
                return
            await self._reject(scope, send)

    async def _reject(self, scope: dict, send: Any) -> None:
        """返回 413（不读 body）。"""
        logger.warning("[api] 请求体超过上限已拒绝 path=%s limit=%d", scope.get("path"), self.max_bytes)
        body = json.dumps(
            {"detail": f"请求体不能超过 {self.max_bytes // 1024}KB"},
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
