"""限流逻辑单元测试。"""
import os
import time

# 单进程内存限流依赖 monotonic 时钟，测试用同进程实例即可
from app.core.rate_limit import RateLimiter


def test_ip_limit_allows_within_quota():
    rl = RateLimiter()
    for _ in range(5):
        ok, _ = rl.hit("k", limit=5, window=60)
        assert ok is True


def test_ip_limit_blocks_over_quota():
    rl = RateLimiter()
    for _ in range(3):
        rl.hit("k", limit=3, window=60)
    ok, retry = rl.hit("k", limit=3, window=60)
    assert ok is False
    assert retry >= 1


def test_window_reset_after_expiry():
    rl = RateLimiter()
    for _ in range(2):
        rl.hit("k", limit=2, window=1)
    # 窗口 1 秒过期后应重新计数（用极小窗口，sleep 等待）
    time.sleep(1.1)
    ok, _ = rl.hit("k", limit=2, window=1)
    assert ok is True


def test_different_keys_independent():
    rl = RateLimiter()
    for _ in range(3):
        rl.hit("a", limit=3, window=60)
    ok, _ = rl.hit("b", limit=3, window=60)
    assert ok is True


def test_gc_evicts_expired_buckets():
    rl = RateLimiter()
    rl.hit("old", limit=1, window=1)
    time.sleep(1.1)
    rl._last_gc = 0.0  # 强制触发 GC
    rl.hit("new", limit=1, window=60)
    assert "old" not in rl._store
    assert "new" in rl._store
