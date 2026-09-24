"""题干文本解析：全站唯一的「空位数」口径。

填空题的答案按空位顺序存储（`[["等价答案1a", "等价答案1b"], ["答案2"]]`），判分要求
`len(correct_answer) == len(user_answer)`，而前端按题干里**连续两个及以上下划线**渲染输入框
（`frontend/src/composables/useAnswerDraft.ts` 的 `/_{2,}/g`）。

因此「题干空位数」必须只有一份实现：Excel 导入路径（`utils/excel.py`）与手工建题/改题路径
（`services/question_service.py`）都要用它做交叉校验 —— 两条路径各持一份实现时，其中一条
会漏校，从而存下「答案空位数与题干不一致、任何作答都判错」的题（已实跑复现）。
"""

from __future__ import annotations

import re

# 连续 2 个及以上下划线算一个空位（与前端 /_{2,}/g 一致）
_BLANK_RE = re.compile(r"_{2,}")


def count_blanks(question_text: str | None) -> int:
    """统计题干中的空位数（连续 2 个及以上下划线算一个空）。

    题干没有下划线时按 1 个空处理，兼容「答案不在题干中留空位」的写法。

    Args:
        question_text: 题干原文（允许 None，按空串处理）。

    Returns:
        空位数（至少 1）。
    """
    return len(_BLANK_RE.findall(question_text or "")) or 1
