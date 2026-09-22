"""考试业务实现包（按职责拆分）。

对外统一入口仍是 `app.services.exam_service`（门面模块），
本包内模块只服务于该门面，请勿跨包直接依赖内部模块。
"""

from __future__ import annotations
