"""Excel 解析单元测试。"""

from io import BytesIO

from openpyxl import Workbook, load_workbook

from app.utils.excel import EXAMPLE_PREFIX, build_template, parse_workbook


def test_build_template_returns_xlsx():
    buf = build_template()
    data = buf.getvalue()
    assert data[:2] == b"PK"  # xlsx 是 zip


def test_parse_empty_workbook():
    wb = Workbook()
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    result = parse_workbook(buf)
    assert result.total == 0
    assert result.rows == []


def test_parse_template_skips_example_rows():
    """模板内置示例题（供用户参考格式）必须被跳过：否则用户不删示例行就会把示例题导入题库。"""
    buf = build_template()
    result = parse_workbook(buf)
    assert result.total == 0, "示例行不应进入导入队列"
    assert result.type_dist == {}
    assert result.errors == []

    # 示例行仍留在模板里（可读性不受影响），只是带上了可识别前缀
    wb = load_workbook(BytesIO(build_template().getvalue()))
    assert str(wb["单选题"].cell(row=2, column=1).value).startswith(EXAMPLE_PREFIX)


def test_parse_single_choice_row():
    wb = Workbook()
    ws = wb.active
    ws.title = "单选题"
    ws.append(["题干", "选项", "答案", "解析", "难度", "知识点标签", "分值", "所属分组ID"])
    ws.append(["HTTP 默认端口？", "21\n80\n443\n8080", "B", "HTTP默认80", 2, "网络", 2, ""])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    result = parse_workbook(buf)
    assert result.total == 1
    row = result.rows[0]
    assert row.type == "单选题"
    assert row.valid is True


def test_parse_single_choice_rejects_multi_letter_answer():
    """单选题答案必须是单个字母（与手动创建路径同口径），否则会存下永远判错的题。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "单选题"
    ws.append(["题干", "选项", "答案", "解析", "难度", "知识点标签", "分值", "所属分组ID"])
    ws.append(["HTTP 默认端口？", "21\n80\n443\n8080", "AB", "", 2, "网络", 2, ""])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    result = parse_workbook(buf)
    assert result.total == 1
    assert result.rows[0].valid is False
    assert "单选题答案必须是一个选项字母" in result.rows[0].error


def test_parse_multi_choice_accepts_multi_letter_answer():
    """多选题仍应接受多字母答案（且排序归一）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "多选题"
    ws.append(["题干", "选项", "答案", "解析", "难度", "知识点标签", "分值", "所属分组ID"])
    ws.append(["哪些是传输层协议？", "TCP\nUDP\nIP\nHTTP", "BA", "", 2, "网络", 2, ""])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    result = parse_workbook(buf)
    row = result.rows[0]
    assert row.valid is True
    assert row.answer == "AB"


def test_parse_judgement_row():
    wb = Workbook()
    ws = wb.active
    ws.title = "判断题"
    ws.append(["题干", "答案", "解析", "难度", "知识点标签", "分值", "所属分组ID"])
    ws.append(["HTTP 默认端口是80", "正确", "", 1, "网络", 1, ""])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    result = parse_workbook(buf)
    assert result.total == 1
    assert result.rows[0].type == "判断题"


def test_parse_fill_row():
    wb = Workbook()
    ws = wb.active
    ws.title = "填空题"
    ws.append(["题干", "答案", "解析", "难度", "知识点标签", "分值", "所属分组ID"])
    ws.append(["TCP三次握手用__包", "SYN/同步|ACK", "", 2, "网络", 2, ""])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    result = parse_workbook(buf)
    assert result.total == 1
    assert result.rows[0].type == "填空题"


def test_parse_options_supports_pipe_separator():
    """单行用 | 分隔（无 A. 前缀）时按 | 拆分：原实现把判断放在「结果非空」之后，分支不可达。"""
    from app.utils.excel import _parse_options

    assert _parse_options("甲|乙") == ["甲", "乙"]
    assert _parse_options("A.甲\nB.乙") == ["甲", "乙"]
