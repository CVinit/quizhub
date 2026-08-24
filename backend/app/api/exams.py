"""考试路由（用户端 + 管理端）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_admin, require_super
from app.database import get_db
from app.models.user import User
from app.schemas.exam import (
    ExamAnswerIn, ExamCreateIn, ExamUpdateIn, MockConfigIn, PaperTemplateIn, ReviewIn,
)
from app.services import exam_service, review_service
from app.services.audit_service import log as audit_log

router = APIRouter(tags=["exam"])


# ---------- 用户端 ----------
@router.get("/exams/available")
def available(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return exam_service.list_available(db, user)


@router.post("/exams/mock/start")
def start_mock(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return exam_service.start_mock_exam(db, user)


@router.post("/exams/{exam_id}/start")
def start_exam(exam_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return exam_service.start_exam(db, user, exam_id)


@router.post("/exams/session/{sid}/answer")
def submit_answer(sid: int, payload: ExamAnswerIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return exam_service.submit_answer(db, user, sid, payload.question_id, payload.answer, payload.version)


@router.post("/exams/session/{sid}/submit")
def submit_exam(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return exam_service.submit_exam(db, user, sid)


@router.get("/exams/session/{sid}/result")
def get_result(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return exam_service.get_result(db, user, sid)


@router.get("/exams/session/{sid}/detail")
def session_detail(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return exam_service.session_detail(db, user, sid)


# ---------- 管理端：试卷模板 ----------
@router.get("/admin/exam-templates")
def list_templates(db: Session = Depends(get_db), _user: User = Depends(require_admin)):
    return exam_service.list_templates(db)


@router.post("/admin/exam-templates/preview-paper")
def preview_paper(payload: dict, db: Session = Depends(get_db), _user: User = Depends(require_admin)):
    return exam_service.preview_paper(db, payload)


@router.post("/admin/exam-templates", status_code=status.HTTP_201_CREATED)
def create_template(payload: PaperTemplateIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    res = exam_service.create_template(db, payload, user)
    audit_log(db, user.id, "exam_template.create", "paper_template", res.get("id"), {"name": payload.name})
    return res


# ---------- 管理端：正式考试 ----------
@router.get("/admin/exams")
def list_exams(db: Session = Depends(get_db), _user: User = Depends(require_admin)):
    return exam_service.list_exams(db)


@router.post("/admin/exams", status_code=status.HTTP_201_CREATED)
def create_exam(payload: ExamCreateIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    res = exam_service.create_exam(db, payload, user)
    audit_log(db, user.id, "exam.create", "exam", res.get("id"), {"name": payload.name, "type": payload.type})
    return res


@router.put("/admin/exams/{exam_id}")
def update_exam(exam_id: int, payload: ExamUpdateIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    res = exam_service.update_exam(db, exam_id, payload.model_dump(exclude_unset=True))
    audit_log(db, user.id, "exam.update", "exam", exam_id, payload.model_dump(exclude_unset=True))
    return res


@router.post("/admin/exams/{exam_id}/publish")
def publish_exam(exam_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    res = exam_service.publish_exam(db, exam_id)
    audit_log(db, user.id, "exam.publish", "exam", exam_id)
    return res


@router.get("/admin/exam-results")
def list_results(exam_id: int | None = None, db: Session = Depends(get_db), _user: User = Depends(require_admin)):
    return exam_service.list_results(db, exam_id)


# ---------- 管理端：模拟考试设置 ----------
@router.get("/admin/mock-config")
def get_mock_config(db: Session = Depends(get_db), _user: User = Depends(require_admin)):
    return exam_service.get_mock_config_full(db)


@router.put("/admin/mock-config")
def save_mock_config(payload: MockConfigIn, db: Session = Depends(get_db), _user: User = Depends(require_admin)):
    return exam_service.save_mock_config(db, payload.config)


# ---------- 管理端：简答复核 ----------
@router.get("/admin/review/pending")
def list_pending_reviews(db: Session = Depends(get_db), _user: User = Depends(require_admin)):
    return review_service.list_pending(db)


@router.post("/admin/review/{review_id}")
def do_review(review_id: int, payload: ReviewIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    res = review_service.review(db, review_id, payload.verdict, payload.partial_score, user)
    audit_log(db, user.id, "review.submit", "short_answer_review", review_id, {"verdict": payload.verdict})
    return res


@router.post("/admin/exams/{exam_id}/publish-results")
def publish_results(exam_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    res = review_service.publish_results(db, exam_id)
    audit_log(db, user.id, "review.publish_results", "exam", exam_id, {"published": res.get("published")})
    return res
