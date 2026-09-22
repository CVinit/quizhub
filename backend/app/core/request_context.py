"""请求级上下文：把当前请求的客户端 IP 暴露给拿不到 `Request` 的深层服务。

审计日志（`services.audit_service.log`）在服务层被调用，无法直接取得 FastAPI 的
`Request`；把 `Request` 或 IP 逐层透传会污染十几个业务函数签名（而且服务层不应依赖
传输层）。这里由 HTTP 中间件写入一个 ContextVar，服务层按需读取：

- 每个请求在自己的 asyncio 上下文中处理，设置不会串到其它请求；
- 同步路由跑在 anyio 线程池里，anyio 会把上下文复制进工作线程，因此同样可读。

IP 的获取口径与限流保持一致（`core.rate_limit.get_client_ip`，受 `TRAINING_TRUST_PROXY`
控制是否信任 `X-Forwarded-For`）。
"""

from __future__ import annotations

from contextvars import ContextVar

_request_ip: ContextVar[str] = ContextVar("request_client_ip", default="")


def set_request_ip(ip: str) -> None:
    """由 HTTP 中间件在每个请求开始时写入。"""
    _request_ip.set(ip)


def get_request_ip() -> str:
    """读取当前请求的客户端 IP；不在请求上下文时返回空串。"""
    return _request_ip.get()
