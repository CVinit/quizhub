"""判分逻辑：6 类题型。

- 单选/判断：等值比较
- 多选：排序后比对，无部分分
- 填空：逐空比对（每空可有多个等价答案，大小写不敏感），全对才算对
- 拖拽：逐对键值相等且数量相等，无部分分
- 简答：返回 None（待自评/人工复核）

answer 规范（与 import_service 一致）：
- 单选 "A"；多选 "ABC"；判断 "正确"/"错误"
- 填空 [["SYN","同步"],["ACK"]] —— 每个元素是一个空的所有等价答案列表
- 拖拽 {"left":"right"}
- 简答 长文本
"""
from __future__ import annotations

from typing import Any


def grade(question_type: str, correct_answer: Any, user_answer: Any) -> bool | None:
    """返回 True/False，或 None（简答待评）。"""
    if question_type in ("单选题", "判断题"):
        return _norm_str(user_answer) == _norm_str(correct_answer)

    if question_type == "多选题":
        ua = sorted(_norm_str(user_answer))
        ca = sorted(_norm_str(correct_answer))
        return ua == ca

    if question_type == "填空题":
        return _grade_fill(correct_answer, user_answer)

    if question_type == "拖拽题":
        return _grade_drag(correct_answer, user_answer)

    if question_type == "简答题":
        return None

    return False


def _norm_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip().upper().replace(" ", "")


def _grade_fill(correct_answer: Any, user_answer: Any) -> bool:
    """correct_answer: list[list[str]]（每空等价答案列表）。
    user_answer: list[str]（每空用户答案）或单字符串。
    """
    if not isinstance(correct_answer, list):
        return False
    if isinstance(user_answer, str):
        user_answer = [user_answer]
    if not isinstance(user_answer, list):
        return False
    if len(correct_answer) != len(user_answer):
        return False
    for blanks, ua in zip(correct_answer, user_answer):
        if not isinstance(blanks, list):
            blanks = [blanks]
        ua_norm = _norm_str(ua)
        if ua_norm not in [_norm_str(b) for b in blanks]:
            return False
    return True


def _grade_drag(correct_answer: Any, user_answer: Any) -> bool:
    if not isinstance(correct_answer, dict) or not isinstance(user_answer, dict):
        return False
    if len(correct_answer) != len(user_answer):
        return False
    for k, v in correct_answer.items():
        if _norm_str(user_answer.get(k)) != _norm_str(v):
            return False
    return True
