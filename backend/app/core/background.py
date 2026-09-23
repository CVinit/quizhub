"""后台任务队列协议：让服务层不必依赖 FastAPI 的 `BackgroundTasks`。

服务层只用到 `add_task`，而 FastAPI 的 `BackgroundTasks` 在结构上满足本协议，
因此路由可以原样传参，领域层则不必 import 传输框架（与 `core/status.py` 同思路）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class BackgroundTaskQueue(Protocol):
    """与 FastAPI `BackgroundTasks` 结构兼容的最小接口。"""

    def add_task(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """登记一个响应返回后执行的任务。"""
        ...
