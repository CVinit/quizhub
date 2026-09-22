"""限流：固定窗口计数器（线程安全，单进程内存）。

匹配 SQLite + 单 uvicorn 进程的轻量化架构，无需 Redis。
若生产改多 worker（gunicorn -w N），需切换为分布式后端（如 Redis），否则各 worker 计数独立。
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

from fastapi import HTTPException, Request, status

# 是否信任反向代理头 X-Forwarded-For。默认不信任（裸跑 uvicorn 时安全），
# 仅当确认上游 Nginx 已用 `proxy_set_header X-Forwarded-For $remote_addr;`
# 覆盖该头（防伪造）时，再经 TRAINING_TRUST_PROXY=true 显式开启。
_TRUST_PROXY = os.getenv("TRAINING_TRUST_PROXY", "false").lower() in ("1", "true", "yes", "on")


def get_client_ip(request: Request) -> str:
    """获取真实客户端 IP。"""
    if _TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


class _Bucket:
    __slots__ = ("count", "reset_at")

    def __init__(self, now: float, window: int) -> None:
        self.count = 1
        self.reset_at = now + window


class RateLimiter:
    """固定窗口计数器。键为业务拼接字符串，如 'login:ip:1.2.3.4'。"""

    def __init__(self) -> None:
        self._store: dict[str, _Bucket] = {}
        self._lock = threading.Lock()
        self._last_gc = 0.0

    def hit(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        """记一次请求。返回 (是否放行, 建议重试等待秒数)。"""
        now = time.monotonic()
        with self._lock:
            # 每 60 秒清理一次过期桶，避免内存无限增长
            if now - self._last_gc > 60:
                self._gc(now)
                self._last_gc = now
            bucket = self._store.get(key)
            if bucket is None or now >= bucket.reset_at:
                self._store[key] = _Bucket(now, window)
                return True, 0
            bucket.count += 1
            if bucket.count > limit:
                return False, max(1, int(bucket.reset_at - now))
            return True, 0

    def _gc(self, now: float) -> None:
        expired = [k for k, b in self._store.items() if now >= b.reset_at]
        for k in expired:
            del self._store[k]

    def reset(self) -> None:
        """清空全部计数（测试隔离用：固定窗口是进程内单例，会跨用例累积）。"""
        with self._lock:
            self._store.clear()
            self._last_gc = 0.0


limiter = RateLimiter()


def _deny(scope: str, retry: int) -> None:
    raise HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"请求过于频繁，请稍后再试（{scope}）",
        headers={"Retry-After": str(retry)},
    )


def check(key: str, limit: int, window: int, scope: str = "操作") -> None:
    """通用限流：对给定 key 计数，超限抛 429。

    供路由内部按 email/username/user_id 等细粒度维度限流。
    """
    allowed, retry = limiter.hit(key, limit, window)
    if not allowed:
        _deny(scope, retry)


def ip_limit(scope: str, limit: int, window: int) -> Callable[..., None]:
    """FastAPI 依赖：按客户端 IP 限流。

    用法：`_rl: None = Depends(ip_limit("login", 10, 60))`
    """

    def _dep(request: Request) -> None:
        check(f"{scope}:ip:{get_client_ip(request)}", limit, window, scope)

    return _dep
