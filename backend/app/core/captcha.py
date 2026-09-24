"""图形验证码：纯点阵 SVG + 内存单次消费，无第三方依赖（无需 Pillow）。

设计：
- 验证码为 4 位数字，用内置 5×7 点阵字体渲染成 SVG 像素图。
- 单进程内存存储，id→(answer, expire_at, consumed)，3 分钟有效、单次消费。
- 返回 (captcha_id, data_uri)，data_uri 是可直接 <img :src> 的 SVG。
- 与 app/core/rate_limit 同为单进程方案；多 worker 需切 Redis。

**渲染必须是每次实例随机的**：SVG 会原样返回给客户端，若渲染结果只由答案决定
（例如拿答案当伪随机种子），攻击者离线枚举 10⁴ 个答案建「图像 → 答案」反查表即可
100% 破解，人机校验形同虚设。因此干扰线/抖动/噪点一律用 `random.SystemRandom()`
取值：同一答案每次渲染都不同，反查表不可用。
"""

from __future__ import annotations

import base64
import random
import secrets
import threading
import time

# 5×7 点阵数字 0-9（每行 5 位，7 行）。1=亮像素。
_DIGITS: list[list[str]] = [
    # 0
    ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    # 1
    ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    # 2
    ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    # 3
    ["01110", "10001", "00001", "00110", "00001", "10001", "01110"],
    # 4
    ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    # 5
    ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    # 6
    ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    # 7
    ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    # 8
    ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    # 9
    ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
]

_CODE_LEN = 4
_TTL = 180  # 3 分钟有效
_BRAND = "#E60012"


class _Entry:
    __slots__ = ("answer", "expire_at", "consumed")

    def __init__(self, answer: str, now: float) -> None:
        self.answer = answer
        self.expire_at = now + _TTL
        self.consumed = False


class CaptchaStore:
    def __init__(self) -> None:
        self._store: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self._last_gc = 0.0

    def generate(self) -> tuple[str, str]:
        """生成一对 (captcha_id, data_uri)。"""
        answer = "".join(secrets.choice("0123456789") for _ in range(_CODE_LEN))
        cid = secrets.token_urlsafe(16)
        now = time.monotonic()
        with self._lock:
            if now - self._last_gc > 60:
                self._gc(now)
                self._last_gc = now
            self._store[cid] = _Entry(answer, now)
        return cid, _render_svg(answer)

    def verify(self, captcha_id: str, user_answer: str) -> bool:
        """校验并单次消费。空 id/答案一律 False。"""
        if not captcha_id or not user_answer:
            return False
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(captcha_id)
            if entry is None or entry.consumed or now >= entry.expire_at:
                if entry is not None:
                    del self._store[captcha_id]
                return False
            ok = entry.answer == user_answer.strip()
            # 无论对错都消费，防止穷举重放
            entry.consumed = True
            del self._store[captcha_id]
            return ok

    def _gc(self, now: float) -> None:
        expired = [k for k, e in self._store.items() if now >= e.expire_at or e.consumed]
        for k in expired:
            del self._store[k]

    def reset(self) -> None:
        """清空全部验证码（测试隔离用：单例状态会跨用例累积）。"""
        with self._lock:
            self._store.clear()
            self._last_gc = 0.0


store = CaptchaStore()


def _render_svg(answer: str) -> str:
    """把 4 位数字渲染成带噪点/干扰线的 SVG 像素图，转 data-uri。

    所有扰动都取 `random.SystemRandom()`（不可预测、与答案无关），因此同一答案
    每次渲染结果都不同 —— 这是防止「离线枚举答案建反查表」的关键，不能改回
    由答案派生种子的伪随机。
    """
    cell = 12  # 每像素 12px
    gap = 4  # 字符间距 px
    cols = 5
    rows = 7
    char_w = cols * cell
    char_h = rows * cell
    pad_x = 16
    pad_y = 12
    width = pad_x * 2 + len(answer) * char_w + (len(answer) - 1) * gap
    height = pad_y * 2 + char_h

    rng = random.SystemRandom()
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
    ]
    # 背景
    parts.append(f'<rect width="{width}" height="{height}" fill="#f4f4f5"/>')

    # 干扰线
    for _ in range(4):
        parts.append(
            f'<line x1="{rng.randint(0, width)}" y1="{rng.randint(0, height)}" '
            f'x2="{rng.randint(0, width)}" y2="{rng.randint(0, height)}" '
            f'stroke="{_BRAND}33" stroke-width="1"/>'
        )

    # 像素点阵
    for idx, ch in enumerate(answer):
        glyph = _DIGITS[int(ch)]
        ox = pad_x + idx * (char_w + gap)
        # 轻微随机垂直抖动，增加机器识别难度
        oy = pad_y + rng.randint(-3, 3)
        for r, row_bits in enumerate(glyph):
            for c, bit in enumerate(row_bits):
                if bit == "1":
                    x = ox + c * cell
                    y = oy + r * cell
                    parts.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{_BRAND}"/>')

    # 随机噪点
    for _ in range(40):
        x = rng.randint(0, width - 2)
        y = rng.randint(0, height - 2)
        parts.append(f'<circle cx="{x}" cy="{y}" r="1" fill="#90939966"/>')

    parts.append("</svg>")
    svg = "".join(parts)
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"
