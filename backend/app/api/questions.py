"""题库与上传路由（管理端）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import dept_scope_ids, require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.question import (
    QuestionBankCreate,
    QuestionBankOut,
    QuestionBankUpdate,
    QuestionCreate,
    QuestionOut,
    QuestionUpdate,
)
from app.services import import_service, question_service
from app.services.audit_service import log as audit_log
from app.services.system_service import get_settings
from app.utils.excel import build_template

router = APIRouter(prefix="/admin", tags=["questions"])


# ---------- 题库来源 ----------
@router.get("/question-banks")
def list_banks(
    practice_enabled: bool | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """题库列表。practice_enabled 省略时返回全部（管理端需管理已关闭的题库）。"""
    return question_service.list_banks(db, dept_scope_ids(db, user), practice_enabled)


@router.post("/question-banks", status_code=status.HTTP_201_CREATED, response_model=QuestionBankOut)
def create_bank(payload: QuestionBankCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    b = question_service.create_bank(db, payload, dept_scope_ids(db, user))
    audit_log(db, user.id, "question_bank.create", "question_bank", b.id, {"name": b.name})
    return b


@router.put("/question-banks/{bank_id}", response_model=QuestionBankOut)
def update_bank(
    bank_id: int,
    payload: QuestionBankUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """更新题库：改名 / 练习开关（practice_enabled）。"""
    b = question_service.update_bank(db, bank_id, payload, dept_scope_ids(db, user))
    audit_log(
        db,
        user.id,
        "question_bank.update",
        "question_bank",
        bank_id,
        payload.model_dump(exclude_unset=True),
    )
    # 返回完整题库对象并由 schema 收敛字段：原实现手拼 dict 漏了 group_id，
    # 与前端 `QuestionBank` 类型不符（类型上 group_id 是必填）。
    return b


@router.delete("/question-banks/{bank_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bank(bank_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """删除题库及其题目；题目被考试引用时拒绝（409）。"""
    question_service.delete_bank(db, bank_id, dept_scope_ids(db, user))
    audit_log(db, user.id, "question_bank.delete", "question_bank", bank_id, None)
    return None


# ---------- 题目 CRUD ----------
@router.get("/questions")
def list_questions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    type: str | None = None,
    bank_id: int | None = None,
    group_id: int | None = None,
    difficulty: int | None = None,
    keyword: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    rows, total = question_service.list_questions(
        db,
        page,
        page_size,
        type,
        bank_id,
        group_id,
        difficulty,
        keyword,
        dept_scope_ids(db, user),
    )
    return {"total": total, "page": page, "page_size": page_size, "items": rows}


@router.get("/question-type-stats")
def question_type_stats(
    bank_ids: str = "",
    group_ids: str = "",
    tags: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """各题型可用题量统计（组卷来源筛选），供题型配比编辑时提示可用余量。"""
    split_ids = lambda s: [int(x) for x in s.split(",") if x.strip().isdigit()]  # noqa: E731
    split_tags = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
    return question_service.type_stats(
        db,
        split_ids(bank_ids),
        split_ids(group_ids),
        split_tags(tags),
        dept_scope_ids(db, user),
    )


@router.post("/questions", status_code=status.HTTP_201_CREATED, response_model=QuestionOut)
def create_question(payload: QuestionCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    q = question_service.create_question(db, payload, dept_scope_ids(db, user))
    audit_log(db, user.id, "question.create", "question", q.id, {"type": payload.type})
    return q


@router.put("/questions/{qid}", response_model=QuestionOut)
def update_question(
    qid: int, payload: QuestionUpdate, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    q = question_service.update_question(db, qid, payload, dept_scope_ids(db, user))
    audit_log(db, user.id, "question.update", "question", qid, payload.model_dump(exclude_unset=True))
    return q


@router.delete("/questions/{qid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(qid: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    question_service.delete_question(db, qid, dept_scope_ids(db, user))
    audit_log(db, user.id, "question.delete", "question", qid)


# ---------- 上传 ----------
@router.get("/upload/template")
def download_template(_user: User = Depends(require_admin)):
    buf = build_template()
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''%E9%A2%98%E5%BA%93%E5%AF%BC%E5%85%A5%E6%A8%A1%E6%9D%BF.xlsx"
        },
    )


@router.post("/upload/preview")
async def upload_preview(
    file: UploadFile = File(...),
    group_id: int | None = Form(None),
    bank_id: int | None = Form(None),
    bank_name: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    # 上传频率限制：单管理员 20 次/小时（防恶意/误操作大文件刷接口）
    from app.core.rate_limit import check

    check(f"upload-preview:user:{user.id}", 20, 3600, "题库上传")
    # 上传大小限制：读取前校验 Content-Length 与系统设置 upload_max_size_mb
    settings = get_settings(db, "upload")
    allowed_ext = {
        ext.strip().lower()
        for ext in settings.get("upload_allowed_ext", ".xlsx").replace("，", ",").split(",")
        if ext.strip()
    }
    filename = (file.filename or "").lower()
    if not any(filename.endswith(ext) for ext in allowed_ext):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件扩展名不在允许列表内")
    max_mb = float(settings.get("upload_max_size_mb", "10") or "10")
    max_bytes = int(max_mb * 1024 * 1024)
    declared = file.size or 0
    if declared and declared > max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"文件超过上限 {max_mb}MB")
    # 分块读取，超过上限即中止，避免一次性 read() 耗尽内存
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"文件超过上限 {max_mb}MB")
        chunks.append(chunk)
    content = b"".join(chunks)
    scope = dept_scope_ids(db, user)
    try:
        # openpyxl 解析大工作簿是 CPU 密集型同步工作；在 async 路由内直接调用会
        # 阻塞事件循环，导致其它请求全部挂起。放到线程池执行。
        return await run_in_threadpool(
            import_service.preview, db, content, group_id, bank_id, bank_name, user.id, scope
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/upload/import")
def upload_import(
    confirm_token: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    res = import_service.do_import(db, confirm_token, user.id, dept_scope_ids(db, user))
    audit_log(db, user.id, "question.import", "questions", "", {"success": res.success, "failed": res.failed})
    return res
