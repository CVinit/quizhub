"""带容量上限的进程内 TTL 缓存。

用于「上传预览 → 确认导入」两步流程的暂存。原实现是裸 dict + 仅在写入时
清理过期项，存在两个问题：

1. **内存无界**：条目只在 `preview()` 内被清理，反复预览却不导入时缓存只增不减，
   单次最多可积压上万行，构成内存耗尽（DoS）面；
2. **过期判断分散**：每个使用方各自比较时间戳，容易漏判。

本模块把「容量上限 + TTL + 惰性过期」收敛到一处。注意它仍是**单进程**状态：
多 worker 部署下 token 不共享，需要用共享存储替换（参见 README 部署说明）。
"""

from __future__ import annotations

import threading
import time
from typing import Generic, TypeVar

T = TypeVar("T")


class BoundedTTLCache(Generic[T]):
    """线程安全的 LRU-近似 TTL 缓存，容量与存活时间双重受限。"""

    __slots__ = ("_store", "_lock", "_ttl", "_maxsize")

    def __init__(self, maxsize: int, ttl: float) -> None:
        """初始化缓存。

        Args:
            maxsize: 最大条目数，超出时淘汰最旧条目。
            ttl: 条目存活秒数，读取与写入时都会检查。
        """
        if maxsize <= 0:
            raise ValueError("maxsize 必须为正整数")
        if ttl <= 0:
            raise ValueError("ttl 必须为正数")
        self._store: dict[str, tuple[T, float, float]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        self._maxsize = maxsize

    def put(self, key: str, payload: T, owner: float = 0.0) -> None:
        """写入条目，并在必要时淘汰过期项与最旧项。

        Args:
            key: 缓存键（如 confirm_token）。
            payload: 要缓存的值。
            owner: 归属标识（通常是 user_id），随条目一起保存用于鉴权。
        """
        now = time.monotonic()
        with self._lock:
            self._evict_expired(now)
            self._store[key] = (payload, owner, now)
            # 容量兜底：按写入时间淘汰最旧条目，保证内存占用有上界
            while len(self._store) > self._maxsize:
                oldest = min(self._store, key=lambda k: self._store[k][2])
                self._store.pop(oldest, None)

    def take(self, key: str) -> tuple[T, float] | None:
        """取出并删除条目（单次消费）；过期或不存在返回 None。

        Args:
            key: 缓存键。

        Returns:
            (payload, owner) 元组；未命中返回 None。
        """
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            payload, owner, created = entry
            # 无论是否过期都移除：过期条目不应再被消费
            self._store.pop(key, None)
            if now - created > self._ttl:
                return None
            return payload, owner

    def peek(self, key: str) -> tuple[T, float] | None:
        """只读取条目而不消费（TTL 内有效）。

        供「先校验、后消费」的流程使用：调用方可在真正 take() 之前完成
        归属/数据范围校验，避免一次非法请求就把上传者本人的预览作废。

        Args:
            key: 缓存键。

        Returns:
            (payload, owner) 元组；不存在或已过期返回 None。
        """
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if now - entry[2] > self._ttl:
                self._store.pop(key, None)
                return None
            return entry[0], entry[1]

    def peek_owner(self, key: str) -> float | None:
        """只读取归属标识而不消费，用于越权检查前判断。

        Args:
            key: 缓存键。

        Returns:
            归属标识；不存在或已过期返回 None。
        """
        entry = self.peek(key)
        return None if entry is None else entry[1]

    def discard(self, key: str) -> None:
        """显式丢弃条目（用于失败路径，避免敏感数据滞留）。"""
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """清空缓存（测试用）。"""
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def _evict_expired(self, now: float) -> None:
        """调用方需已持有锁。"""
        expired = [k for k, v in self._store.items() if now - v[2] > self._ttl]
        for key in expired:
            self._store.pop(key, None)
