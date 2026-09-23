"""上传读取：扩展名白名单 + 体积上限 + 分块读取的单一实现。

`api/questions.py`（题库导入）与 `api/users.py`（用户导入）原先各自抄了一份
「Content-Length 预检 + 1MB 分块累计超限」，两处已经分叉（扩展名来源不同）：
改一处漏一处就会留下一个无上限的上传入口。这里收敛为一份实现。
"""

from __future__ import annotations

from fastapi import HTTPException, UploadFile, status

_CHUNK_SIZE = 1024 * 1024
_DEFAULT_EXT_MESSAGE = "文件扩展名不在允许列表内"


def _human_size(num_bytes: int) -> str:
    """把字节数转成 MB 文案（整数不显示小数点，便于错误提示）。"""
    return f"{num_bytes / (1024 * 1024):g}MB"


async def read_limited(
    file: UploadFile,
    *,
    max_bytes: int,
    allowed_ext: set[str],
    ext_message: str = _DEFAULT_EXT_MESSAGE,
    chunk_size: int = _CHUNK_SIZE,
) -> bytes:
    """按扩展名白名单与体积上限分块读取上传内容。

    先按声明的 Content-Length 快速拒绝，再分块累计校验实际大小 —— 单次
    `await file.read()` 会让超大 body 先进内存，构成内存耗尽面。

    Args:
        file: FastAPI 上传文件对象。
        max_bytes: 允许的最大字节数。
        allowed_ext: 允许的扩展名集合（小写、含点，如 `{".xlsx"}`）。
        ext_message: 扩展名不合法时的错误文案。
        chunk_size: 单次读取块大小。

    Returns:
        文件完整字节。

    Raises:
        HTTPException: 400 扩展名不在白名单；413 声明或实际大小超限。
    """
    filename = (file.filename or "").lower()
    if not any(filename.endswith(ext) for ext in allowed_ext):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, ext_message)

    declared = file.size or 0
    if declared > max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"文件超过上限 {_human_size(max_bytes)}")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"文件超过上限 {_human_size(max_bytes)}")
        chunks.append(chunk)
    return b"".join(chunks)
