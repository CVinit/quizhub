"""限流逻辑单元测试。"""

# 单进程内存限流依赖 monotonic 时钟，测试用同进程实例即可
from app.core import rate_limit
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


def test_window_reset_after_expiry(monkeypatch):
    """窗口过期后重新计数：用假时钟推进，避免真实 sleep 带来的时间依赖（负载高时假失败）。"""
    rl = RateLimiter()
    clock = {"now": 1000.0}
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock["now"])

    for _ in range(2):
        rl.hit("k", limit=2, window=1)
    clock["now"] += 1.1
    ok, _ = rl.hit("k", limit=2, window=1)
    assert ok is True


def test_different_keys_independent():
    rl = RateLimiter()
    for _ in range(3):
        rl.hit("a", limit=3, window=60)
    ok, _ = rl.hit("b", limit=3, window=60)
    assert ok is True


def test_gc_evicts_expired_buckets(monkeypatch):
    """GC 清理过期桶：同样用假时钟推进。"""
    rl = RateLimiter()
    clock = {"now": 2000.0}
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock["now"])

    rl.hit("old", limit=1, window=1)
    clock["now"] += 1.1
    rl._last_gc = 0.0  # 强制触发 GC
    rl.hit("new", limit=1, window=60)
    assert "old" not in rl._store
    assert "new" in rl._store
