"""Excel 题库模板生成与解析。

模板工作簿 Sheet：单选题 / 多选题 / 判断题 / 填空题 / 简答题 / 拖拽题 / 说明。

填空答案约定：按空位顺序用 | 分隔，每空多等价答案用 / 分隔，如 "答案1a/答案1b|答案2"。
拖拽题「题项与正确容器」列：每行 "题项:正确容器"，多对用换行分隔。
"""

from __future__ import annotations

import math
from io import BytesIO
from typing import Any
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from app.schemas.question import UploadPreview, UploadPreviewRow

# openpyxl 在「合法 zip 但非工作簿」时抛出的异常集合。这些都不是 ValueError，
# 路由只捕获 ValueError → 会变成 500，因此统一包装。
_WORKBOOK_OPEN_ERRORS = (KeyError, OSError, BadZipFile, InvalidFileException, ParseError)


def open_workbook(buf: BytesIO):
    """打开 xlsx 工作簿，并把 openpyxl 的解析异常统一转换为 ValueError。

    `validate_workbook_archive` 只校验 zip 结构与解压体积：一个合法 zip 若缺少
    `[Content_Types].xml`（把 .docx/.zip 改名成 .xlsx 是最常见的用户错误），
    openpyxl 抛的是 `KeyError`，而上传路由只把 `ValueError` 转成 400。

    Args:
        buf: 已定位到开头的字节流。

    Returns:
        打开的只读工作簿。

    Raises:
        ValueError: 文件不是可解析的 xlsx 工作簿。
    """
    try:
        return load_workbook(buf, data_only=True, read_only=True)
    except _WORKBOOK_OPEN_ERRORS as exc:
        raise ValueError("上传文件不是有效的 xlsx 工作簿") from exc


SHEET_ORDER = ["单选题", "多选题", "判断题", "填空题", "简答题", "拖拽题"]
PARSE_ROW_MAX = 10000
MAX_WORKBOOK_UNCOMPRESSED = 100 * 1024 * 1024
# 流式解压时的读取块大小：用于在解压过程中逐步计量，避免一次性展开
_DECOMPRESS_CHUNK = 1 << 20
MAX_CELL_CHARS = 10000
TYPE_TO_SHEET = {
    "单选题": "单选题",
    "多选题": "多选题",
    "判断题": "判断题",
    "填空题": "填空题",
    "简答题": "简答题",
    "拖拽题": "拖拽题",
}

# 各 Sheet 表头
HEADERS = {
    "单选题": ["题干", "选项", "答案", "解析", "难度", "知识点标签", "分值", "所属分组ID"],
    "多选题": ["题干", "选项", "答案", "解析", "难度", "知识点标签", "分值", "所属分组ID"],
    "判断题": ["题干", "答案", "解析", "难度", "知识点标签", "分值", "所属分组ID"],
    "填空题": ["题干", "答案", "解析", "难度", "知识点标签", "分值", "所属分组ID"],
    "简答题": ["题干", "参考答案", "解析", "难度", "知识点标签", "分值", "所属分组ID"],
    "拖拽题": ["题干", "题项与正确容器", "解析", "难度", "知识点标签", "分值", "所属分组ID"],
}

# 答案列在表头中的索引（0-based）
ANSWER_COL = {
    "单选题": 2,
    "多选题": 2,
    "判断题": 1,
    "填空题": 1,
    "简答题": 1,
    "拖拽题": 1,
}

HEADER_FILL = PatternFill("solid", fgColor="E60012")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def build_template() -> BytesIO:
    """生成模板 xlsx（含说明 Sheet）。"""
    wb = Workbook()
    wb.remove(wb.active)

    for st in SHEET_ORDER:
        ws = wb.create_sheet(st)
        headers = HEADERS[st]
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center")
        # 列宽
        widths = [40, 50, 20, 30, 8, 20, 8, 12]
        for i, w in enumerate(widths[: len(headers)], 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        # 示例行
        _add_example(ws, st)

    # 说明 Sheet
    ws = wb.create_sheet("说明")
    notes = [
        ["题库导入模板说明", ""],
        ["", ""],
        ["选项格式", "单选/多选：每个选项一行，形如 A.选项内容\\nB.选项内容\\nC.选项内容"],
        ["答案格式-单选", "单个字母，如 A"],
        ["答案格式-多选", "多字母连写，如 ABC（不分先后）"],
        ["答案格式-判断", "正确 或 错误"],
        ["答案格式-填空", "按空位顺序，空与空之间用 | 分隔；每空若有多个等价答案用 / 分隔。如 答案1/答案1b|答案2"],
        ["答案格式-简答", "参考答案长文本（不计入自动判分，由人工/自评）"],
        ["答案格式-拖拽", "题项与正确容器列：每行一对，格式 题项:正确容器，多对换行分隔"],
        ["难度", "1（易）/2（中）/3（难），默认 2"],
        ["知识点标签", "多个标签用英文逗号分隔，如 OpenStack,网络"],
        ["分值", "数字，默认 2"],
        ["所属分组ID", "可留空，留空时使用上传时选择的分组"],
        ["", ""],
        ["转换 Prompt（供豆包/DeepSeek 将 Word 题库转为本 Excel）：", ""],
        [
            "",
            "请把以下 Word 题库按题型分别整理到对应的 Excel Sheet。"
            "每个 Sheet 第一行是表头，从第二行起每题一行。"
            "选项用 A.xxx\\nB.xxx 格式；多选答案连写字母如 ABC；"
            "判断题答案写“正确”或“错误”；"
            "填空题答案按空位顺序用 | 分隔，每空多个等价答案用 / 分隔；"
            "拖拽题在“题项与正确容器”列每行写“题项:正确容器”。"
            "保留题干、解析、难度、知识点标签。输出 .xlsx。",
        ],
    ]
    for row in notes:
        ws.append(row)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 90

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _add_example(ws, sheet_type: str) -> None:
    if sheet_type == "单选题":
        ws.append(
            [
                "以下哪一项是 HTTP 默认端口？",
                "A.21\nB.80\nC.443\nD.8080",
                "B",
                "HTTP 默认 80，HTTPS 默认 443",
                1,
                "网络,基础",
                2,
                "",
            ]
        )
    elif sheet_type == "多选题":
        ws.append(
            [
                "以下属于关系型数据库的有？",
                "A.MySQL\nB.Redis\nC.PostgreSQL\nD.MongoDB",
                "AC",
                "Redis/MongoDB 为 NoSQL",
                2,
                "数据库",
                3,
                "",
            ]
        )
    elif sheet_type == "判断题":
        ws.append(["HTTP 是无状态协议。", "正确", "HTTP 协议本身不保存客户端状态。", 1, "网络", 2, ""])
    elif sheet_type == "填空题":
        ws.append(["TCP 三次握手的第二次报文标志位是 ____ 与 ____。", "SYN/同步|ACK/确认", "SYN+ACK", 2, "网络", 2, ""])
    elif sheet_type == "简答题":
        ws.append(
            ["简述 HTTPS 的工作原理。", "HTTPS = HTTP + TLS。客户端请求服务器证书…", "考察 TLS 握手", 3, "安全", 5, ""]
        )
    elif sheet_type == "拖拽题":
        ws.append(["将协议与默认端口匹配。", "HTTP:80\nHTTPS:443\nSSH:22\nMySQL:3306", "常见端口", 2, "网络", 3, ""])


def parse_workbook(buf: BytesIO) -> UploadPreview:
    """解析上传的工作簿，返回预览（含错误报告）。

    使用 read_only 模式降低内存占用。
    """
    validate_workbook_archive(buf.getvalue())
    buf.seek(0)
    wb = open_workbook(buf)
    rows: list[UploadPreviewRow] = []
    errors: list[dict] = []
    type_dist: dict[str, int] = {}

    try:
        for sheet_name in SHEET_ORDER:
            if sheet_name not in wb.sheetnames:
                continue
            ws = wb[sheet_name]
            # 列是**按位置**取值的，表头一旦被调换/改名，分值、难度、答案会静默错位。
            # 因此先校验第 1 行与模板一致，不一致直接跳过该 Sheet 并给出可见错误。
            row_iter = ws.iter_rows(min_row=1, values_only=True)
            header = next(row_iter, None)
            if not headers_match(header, HEADERS[sheet_name]):
                errors.append(
                    {
                        "sheet": sheet_name,
                        "row": 1,
                        "error": "表头与模板不一致，请下载最新模板后重新填写",
                    }
                )
                continue
            for r_idx, row in enumerate(row_iter, start=2):
                if r_idx > PARSE_ROW_MAX + 1:
                    break
                if not row or all(c is None or str(c).strip() == "" for c in row):
                    continue
                parsed = _parse_row(sheet_name, row, r_idx)
                rows.append(parsed)
                if parsed.valid:
                    type_dist[sheet_name] = type_dist.get(sheet_name, 0) + 1
                else:
                    errors.append({"sheet": sheet_name, "row": r_idx, "error": parsed.error})
                if len(rows) >= PARSE_ROW_MAX:
                    break
            if len(rows) >= PARSE_ROW_MAX:
                break
    finally:
        wb.close()

    # rows 仅返回前 20 条用于预览；all_rows 提供完整结果供导入消费（避免二次解析）
    return UploadPreview(
        rows=rows[:20],
        total=len(rows),
        type_dist=type_dist,
        errors=errors,
        all_rows=rows,
    )


def validate_workbook_archive(content: bytes) -> None:
    """校验上传内容是真正的 xlsx，并限制实际解压体积。

    修复要点：原实现仅累加 zip 中央目录里的 `file_size`，那是**可由攻击者伪造的
    声明值**，与真实解压体积无关，zip bomb 可轻易绕过。这里改为流式真实解压并
    累计字节数，超出上限立即中止——既不信任元数据，也不会把整个炸弹读进内存。

    Args:
        content: 上传文件的原始字节。

    Raises:
        ValueError: 不是合法 xlsx（含魔数校验失败）或解压后超过上限。
    """
    # 魔数校验：xlsx 是 zip，必须以 PK\\x03\\x04 开头。
    # 仅凭文件名后缀判断会让任意文件进入 openpyxl。
    if not content.startswith(b"PK\x03\x04"):
        raise ValueError("上传文件不是有效的 xlsx 工作簿")

    total = 0
    try:
        with ZipFile(BytesIO(content)) as archive:
            for info in archive.infolist():
                # 目录项无需解压
                if info.is_dir():
                    continue
                with archive.open(info) as fh:
                    while chunk := fh.read(_DECOMPRESS_CHUNK):
                        total += len(chunk)
                        if total > MAX_WORKBOOK_UNCOMPRESSED:
                            raise ValueError(
                                f"工作簿解压后超过大小上限 {MAX_WORKBOOK_UNCOMPRESSED // (1024 * 1024)}MB，已拒绝"
                            )
    except BadZipFile:
        raise ValueError("上传文件不是有效的 xlsx 工作簿") from None
    except OSError as exc:
        # 损坏的压缩流（截断、CRC 不符）同样按非法上传处理
        raise ValueError("上传文件已损坏，无法解析") from exc


def headers_match(header: tuple | None, expected: list[str]) -> bool:
    """校验工作表首行是否与模板表头一致（忽略单元格两端空白）。"""
    if header is None:
        return False
    actual = [str(cell).strip() if cell is not None else "" for cell in header]
    return actual[: len(expected)] == expected


def _parse_row(sheet_name: str, row: tuple, r_idx: int) -> UploadPreviewRow:
    """把一行 Excel 解析为标准化题目结构。"""
    qtype = sheet_name  # Sheet 名即题型
    headers = HEADERS[sheet_name]
    cells = list(row) + [None] * (len(headers) - len(row))
    answer_col = ANSWER_COL[sheet_name]
    question_text = str(cells[0] or "").strip()
    answer_raw = cells[answer_col]

    error = ""
    options = left_items = right_items = None
    answer: Any = None

    if any(isinstance(cell, str) and len(cell) > MAX_CELL_CHARS for cell in cells):
        error = "单元格内容过长"
    elif not question_text:
        error = "题干为空"

    if not error and qtype in ("单选题", "多选题"):
        options_text = str(cells[1] or "").strip()
        options = _parse_options(options_text)
        ans = str(answer_raw or "").strip().upper()
        if not options:
            error = "选项为空或格式错误"
        elif not ans:
            error = "答案为空"
        else:
            valid_letters = {chr(ord("A") + i) for i in range(len(options))}
            if not set(ans).issubset(valid_letters):
                error = f"答案 {ans} 超出选项范围 {sorted(valid_letters)}"
            elif qtype == "单选题" and len(ans) != 1:
                # 与 question_service._validate_answer_shape 同口径：单选答案必须恰好一个
                # 字母。放行 "AB" 会存下一道永远判错（grade 对单选做整串比较）的题。
                error = f"单选题答案必须是一个选项字母（当前为 {ans}）"
            else:
                answer = "".join(sorted(ans)) if qtype == "多选题" else ans

    elif not error and qtype == "判断题":
        ans = str(answer_raw or "").strip()
        if ans in ("正确", "对", "A", "T", "true", "是"):
            answer = "正确"
        elif ans in ("错误", "错", "B", "F", "false", "否"):
            answer = "错误"
        else:
            error = "判断题答案必须是 正确/错误"

    elif not error and qtype == "填空题":
        ans = str(answer_raw or "").strip()
        if not ans:
            error = "填空答案为空"
        else:
            # 按空位 | 分隔，每空 / 分隔等价答案
            blanks = [b.strip() for b in ans.split("|") if b.strip()]
            answer = [[x.strip() for x in b.split("/") if x.strip()] for b in blanks]
            # 每个空位都必须至少有一个可选答案，否则该空永远判错（题目实际不可作答）
            if any(not alternatives for alternatives in answer):
                error = "填空答案格式错误：每个空位至少需要一个可选答案（用 / 分隔等价答案）"

    elif not error and qtype == "简答题":
        ans = str(answer_raw or "").strip()
        if not ans:
            error = "参考答案为空"
        answer = ans

    elif not error and qtype == "拖拽题":
        pairs_text = str(answer_raw or "").strip()
        left_items, right_items, mapping = [], [], {}
        for line in pairs_text.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            left, right = line.split(":", 1)
            left, right = left.strip(), right.strip()
            if left and right:
                left_items.append(left)
                right_items.append(right)
                mapping[left] = right
        if not mapping:
            error = "拖拽题题项与正确容器解析为空（格式应为 题项:正确容器，每行一对）"
        else:
            options = None
            answer = mapping

    difficulty = _to_int(cells, _col_index(sheet_name, "难度"), default=2, lo=1, hi=3)
    score, score_error = _parse_score(cells, _col_index(sheet_name, "分值"))
    tags = _parse_tags(cells, _col_index(sheet_name, "知识点标签"))
    analysis_col = _col_index(sheet_name, "解析")
    analysis = str(cells[analysis_col] or "").strip() if analysis_col < len(cells) else ""
    error = error or score_error

    return UploadPreviewRow(
        type=qtype,
        question=question_text,
        options=options,
        left_items=left_items,
        right_items=right_items,
        answer=answer,
        analysis=analysis,
        difficulty=difficulty,
        tags=tags,
        score=score,
        row_index=r_idx,
        valid=not error,
        error=error,
    )


def _parse_options(text: str) -> list[str]:
    """解析 'A.xxx\\nB.xxx' 或 'xxx|yyy' 为列表。"""
    text = text.replace("\\n", "\n")
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    result: list[str] = []
    for p in parts:
        if len(p) >= 2 and p[0].isalpha() and p[1] in ".、:：":
            result.append(p[2:].strip())
        else:
            result.append(p)
    # 若没有 A. 前缀且用 | 分隔
    if not result and "|" in text:
        result = [p.strip() for p in text.split("|") if p.strip()]
    return result


def _parse_tags(cells: list, col: int) -> list[str]:
    if col >= len(cells) or not cells[col]:
        return []
    return [t.strip() for t in str(cells[col]).replace("，", ",").split(",") if t.strip()]


def _col_index(sheet_name: str, header_name: str) -> int:
    return HEADERS[sheet_name].index(header_name)


def _to_int(cells: list, col: int, default: int = 0, lo: int | None = None, hi: int | None = None) -> int:
    try:
        # OverflowError：文本 "inf"/"1e400" 经 float() 后 int() 会抛 OverflowError，
        # 它既不是 ValueError 也不是 TypeError，不捕获会一路冒泡成 500。
        v = int(float(cells[col])) if col < len(cells) and cells[col] not in (None, "") else default
    except (ValueError, TypeError, OverflowError):
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _parse_score(cells: list, col: int, default: float = 2.0) -> tuple[float, str]:
    """解析「分值」为 (值, 行级错误)。

    负数/NaN/Infinity 会被 Pydantic 的 `FiniteFloat` + `ge=0` 拒绝，但那是**整份预览**
    级别的 ValidationError（错误信息还不指向具体行），因此在这里就按行报错并回退默认分。
    """
    if col >= len(cells) or cells[col] in (None, ""):
        return default, ""
    try:
        value = float(cells[col])
    except (ValueError, TypeError, OverflowError):
        return default, f"分值不是数字：{cells[col]!r}"
    if not math.isfinite(value) or value < 0:
        return default, f"分值必须是非负有限数字：{cells[col]!r}"
    return value, ""
