"""题库与上传路由（管理端）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import dept_scope_ids, require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.question import (
    QuestionBankCreate,
    QuestionCreate,
    QuestionUpdate,
)
from app.services import import_service, question_service
from app.services.audit_service import log as audit_log
from app.services.system_service import get_settings
from app.utils.excel import build_template

router = APIRouter(prefix="/admin", tags=["questions"])


# ---------- 题库来源 ----------
@router.get("/question-banks")
def list_banks(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return question_service.list_banks(db, dept_scope_ids(db, user))


@router.post("/question-banks", status_code=status.HTTP_201_CREATED)
def create_bank(payload: QuestionBankCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    b = question_service.create_bank(db, payload, dept_scope_ids(db, user))
    audit_log(db, user.id, "question_bank.create", "question_bank", b.id, {"name": b.name})
    return b


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


@router.post("/questions", status_code=status.HTTP_201_CREATED)
def create_question(payload: QuestionCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    q = question_service.create_question(db, payload, dept_scope_ids(db, user))
    audit_log(db, user.id, "question.create", "question", q.id, {"type": payload.type})
    return q


@router.put("/questions/{qid}")
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
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"文件超过上限 {max_mb}MB")
    # 分块读取，超过上限即中止，避免一次性 read() 耗尽内存
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"文件超过上限 {max_mb}MB")
        chunks.append(chunk)
    content = b"".join(chunks)
    try:
        return import_service.preview(db, content, group_id, bank_id, bank_name, user.id, dept_scope_ids(db, user))
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
