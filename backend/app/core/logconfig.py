"""应用日志配置。

uvicorn 只配置自己的 logger（`uvicorn.*`），不配置 root，因此应用侧
`logging.getLogger("quizhub")` 会传播到「无 handler 的 root」：

- `logger.info(...)` 直接被丢弃（root 默认 WARNING 级，且无 handler）；
- `logger.warning/error` 走 `logging.lastResort`，输出无时间戳、无 logger 名。

这里在应用启动时为 root 挂一个 stdout handler（仅当 root 尚无 handler，
避免与 pytest 的 caplog / uvicorn 的日志配置打架），并把 `quizhub`
logger 的级别交给 `TRAINING_LOG_LEVEL`（默认 INFO）。第三方库仍受 root
的 WARNING 级别约束，不会把 SQL 语句等噪声刷进日志。
"""

from __future__ import annotations

import logging
import os
import sys

_LOGGER_NAME = "quizhub"
_CONFIGURED = False
_DEFAULT_LEVEL = "INFO"
_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging() -> None:
    """配置应用日志（幂等，重复调用无副作用）。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    raw_level = os.getenv("TRAINING_LOG_LEVEL", _DEFAULT_LEVEL).upper()
    # getLevelName 对合法级别名返回 int，对未知名返回 "Level X" 字符串（3.10 兼容）
    resolved = logging.getLevelName(raw_level)
    level = resolved if isinstance(resolved, int) else logging.INFO

    # 仅在 root 无 handler 时安装（force=False）：pytest / uvicorn 已配置时保持原样
    logging.basicConfig(level=logging.WARNING, format=_FORMAT, datefmt=_DATE_FORMAT, stream=sys.stdout)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    _CONFIGURED = True
