"""判分逻辑单元测试。"""

from app.services.grading import grade


def test_single_choice_correct():
    assert grade("单选题", "B", "B") is True


def test_single_choice_wrong():
    assert grade("单选题", "B", "A") is False


def test_single_choice_case_insensitive():
    assert grade("单选题", "B", "b") is True


def test_single_choice_with_spaces():
    assert grade("单选题", "B", " B ") is True


def test_multi_choice_order_insensitive():
    assert grade("多选题", "ABC", "CBA") is True


def test_multi_choice_partial_wrong():
    assert grade("多选题", "ABC", "AB") is False


def test_judge_correct():
    assert grade("判断题", "正确", "正确") is True


def test_judge_wrong():
    assert grade("判断题", "正确", "错误") is False


def test_fill_all_correct():
    correct = [["SYN", "同步"], ["ACK"]]
    assert grade("填空题", correct, ["同步", "ack"]) is True


def test_fill_second_blank_wrong():
    correct = [["SYN", "同步"], ["ACK"]]
    assert grade("填空题", correct, ["同步", "FIN"]) is False


def test_fill_case_insensitive():
    correct = [["TCP"]]
    assert grade("填空题", correct, ["tcp"]) is True


def test_fill_count_mismatch():
    correct = [["A"], ["B"]]
    assert grade("填空题", correct, ["A"]) is False


def test_drag_all_correct():
    correct = {"SYN": "同步", "ACK": "确认"}
    user = {"SYN": "同步", "ACK": "确认"}
    assert grade("拖拽题", correct, user) is True


def test_drag_wrong():
    correct = {"SYN": "同步", "ACK": "确认"}
    user = {"SYN": "确认", "ACK": "同步"}
    assert grade("拖拽题", correct, user) is False


def test_drag_count_mismatch():
    correct = {"SYN": "同步", "ACK": "确认"}
    user = {"SYN": "同步"}
    assert grade("拖拽题", correct, user) is False


def test_short_answer_returns_none():
    assert grade("简答题", "参考答案", "用户作答") is None


def test_short_answer_empty_still_none():
    assert grade("简答题", "参考答案", "") is None
