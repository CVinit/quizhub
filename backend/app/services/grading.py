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

import unicodedata
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


def is_passed(score: float, total_score: float, pass_score: float, *, overtime: bool = False) -> bool:
    """按百分制判定是否及格。

    及格线（ExamDefinition.pass_score / 系统设置 default_pass_score）与前端「及格线」
    输入框（上限 100）都是**百分制**，而成绩是题目原始分累加（Question.score 默认 2 分/题），
    两者量纲不同，必须先归一化再比较：否则 10 题（满分 20）的模拟考即使满分也永远判不及格。
    总分为 0（空卷/异常数据）或超时交卷一律不及格。

    Args:
        score: 得分（题目原始分累加）。
        total_score: 卷面总分（题目原始分累加）。
        pass_score: 及格线（百分制，0~100）。
        overtime: 是否超时交卷；保持「超时即不及格」语义。

    Returns:
        是否及格。
    """
    if overtime or total_score <= 0:
        return False
    # 分数是 Float 累加（且 partial_score 可任意小数），数学上刚好 60% 也可能得到
    # 59.99999999999999 而误判不及格。先按 6 位小数归一，再与及格线比较；
    # 同一口径必须同步到 review_service.publish_results 的 SQL 表达式。
    return round(score * 100.0 / total_score, 6) >= pass_score


def _norm_str(v: Any) -> str:
    """归一化作答文本：NFKC + 去空白 + 大写。

    NFKC 会把全角字符折叠成 ASCII（`Ａ`→`A`、全角空格→普通空格、`１`→`1`），
    原实现只去掉 ASCII 空格，中文输入法下的全角空格/全角字母会让正确答案被判错。
    """
    if v is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(v))
    return normalized.strip().upper().replace(" ", "")


def _grade_fill(correct_answer: Any, user_answer: Any) -> bool:
    """correct_answer: list[list[str]]（每空等价答案列表）。
    user_answer: list[str]（每空用户答案）或单字符串。

    空答案（correct_answer 为空列表）视为无效题，一律判错，避免空 zip 恒真把任意作答判满分。
    """
    if not isinstance(correct_answer, list) or not correct_answer:
        return False
    if isinstance(user_answer, str):
        user_answer = [user_answer]
    if not isinstance(user_answer, list):
        return False
    if len(correct_answer) != len(user_answer):
        return False
    # 长度已在上面显式比较过，strict=True 只是把该不变式写进代码（不会真的触发）
    for blanks, ua in zip(correct_answer, user_answer, strict=True):
        if not isinstance(blanks, list):
            blanks = [blanks]
        ua_norm = _norm_str(ua)
        if ua_norm not in [_norm_str(b) for b in blanks]:
            return False
    return True


def _grade_drag(correct_answer: Any, user_answer: Any) -> bool:
    """空答案（correct_answer 为空 dict）视为无效题，一律判错，避免空 all([]) 恒真。"""
    if not isinstance(correct_answer, dict) or not correct_answer or not isinstance(user_answer, dict):
        return False
    if len(correct_answer) != len(user_answer):
        return False
    return all(_norm_str(user_answer.get(k)) == _norm_str(v) for k, v in correct_answer.items())
