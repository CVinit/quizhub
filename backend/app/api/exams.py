"""考试路由（用户端 + 管理端）。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import dept_scope_ids, get_current_user, require_admin, require_super
from app.database import get_db
from app.models.user import User
from app.schemas.exam import (
    ExamAnswerIn,
    ExamCreateIn,
    ExamUpdateIn,
    MockConfigIn,
    PaperTemplateIn,
    ReviewIn,
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
def submit_answer(
    sid: int, payload: ExamAnswerIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
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
def list_templates(db: Session = Depends(get_db), _user: User = Depends(require_super)):
    return exam_service.list_templates(db)


@router.post("/admin/exam-templates/preview-paper")
def preview_paper(payload: dict, db: Session = Depends(get_db), _user: User = Depends(require_super)):
    return exam_service.preview_paper(db, payload)


@router.post("/admin/exam-templates", status_code=status.HTTP_201_CREATED)
def create_template(payload: PaperTemplateIn, db: Session = Depends(get_db), user: User = Depends(require_super)):
    res = exam_service.create_template(db, payload, user)
    audit_log(db, user.id, "exam_template.create", "paper_template", res.get("id"), {"name": payload.name})
    return res


@router.delete("/admin/exam-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(require_super)):
    exam_service.delete_template(db, template_id)
    audit_log(db, user.id, "exam_template.delete", "paper_template", template_id, None)
    return None


# ---------- 管理端：正式考试 ----------
@router.get("/admin/exams")
def list_exams(
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """考试列表。status 省略时返回全部状态（含已归档）。"""
    return exam_service.list_exams(db, dept_scope_ids(db, user), status_filter)


@router.post("/admin/exams", status_code=status.HTTP_201_CREATED)
def create_exam(payload: ExamCreateIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    res = exam_service.create_exam(db, payload, user, dept_scope_ids(db, user))
    audit_log(db, user.id, "exam.create", "exam", res.get("id"), {"name": payload.name, "type": payload.type})
    return res


@router.put("/admin/exams/{exam_id}")
def update_exam(
    exam_id: int, payload: ExamUpdateIn, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    res = exam_service.update_exam(db, exam_id, payload.model_dump(exclude_unset=True), dept_scope_ids(db, user))
    audit_log(db, user.id, "exam.update", "exam", exam_id, payload.model_dump(exclude_unset=True))
    return res


@router.delete("/admin/exams/{exam_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_exam(exam_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """删除考试（需无作答记录；有记录请改用归档）。"""
    exam_service.delete_exam(db, exam_id, dept_scope_ids(db, user))
    audit_log(db, user.id, "exam.delete", "exam", exam_id, None)
    return None


@router.post("/admin/exams/{exam_id}/archive")
def archive_exam(exam_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """归档考试：对用户隐藏，保留定义与成绩（用于已有作答记录的测试考试）。"""
    res = exam_service.archive_exam(db, exam_id, dept_scope_ids(db, user))
    audit_log(db, user.id, "exam.archive", "exam", exam_id, None)
    return res


@router.post("/admin/exams/{exam_id}/unarchive")
def unarchive_exam(exam_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """取消归档：恢复为已发布。"""
    res = exam_service.unarchive_exam(db, exam_id, dept_scope_ids(db, user))
    audit_log(db, user.id, "exam.unarchive", "exam", exam_id, None)
    return res


@router.post("/admin/exams/{exam_id}/publish")
def publish_exam(
    exam_id: int,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    res = exam_service.publish_exam(db, exam_id, dept_scope_ids(db, user), bg)
    audit_log(db, user.id, "exam.publish", "exam", exam_id)
    return res


@router.get("/admin/exam-results")
def list_results(
    exam_id: int | None = None,
    limit: int = Query(500, ge=1, le=1000),
    outcome: Literal["passed", "failed", "pending", "published"] | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """成绩列表。outcome 省略时返回全部（及格/不及格/待复核/已公布）。"""
    return exam_service.list_results(db, exam_id, dept_scope_ids(db, user), limit, outcome)


# ---------- 管理端：模拟考试设置（全局配置，仅超级管理员）----------
@router.get("/admin/mock-config")
def get_mock_config(db: Session = Depends(get_db), _user: User = Depends(require_super)):
    return exam_service.get_mock_config_full(db)


@router.put("/admin/mock-config")
def save_mock_config(payload: MockConfigIn, db: Session = Depends(get_db), _user: User = Depends(require_super)):
    return exam_service.save_mock_config(db, payload.config)


# ---------- 管理端：简答复核 ----------
@router.get("/admin/review/pending")
def list_pending_reviews(
    limit: int = Query(500, ge=1, le=1000),
    verdict: Literal["pending", "done", "pass", "fail", "partial"] | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """简答复核列表。verdict 省略时默认只看待复核。"""
    return review_service.list_pending(db, dept_scope_ids(db, user), limit, verdict)


@router.post("/admin/review/{review_id}")
def do_review(review_id: int, payload: ReviewIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    res = review_service.review(db, review_id, payload.verdict, payload.partial_score, user, dept_scope_ids(db, user))
    audit_log(db, user.id, "review.submit", "short_answer_review", review_id, {"verdict": payload.verdict})
    return res


@router.post("/admin/exams/{exam_id}/publish-results")
def publish_results(
    exam_id: int,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    res = review_service.publish_results(db, exam_id, dept_scope_ids(db, user), bg)
    audit_log(db, user.id, "review.publish_results", "exam", exam_id, {"published": res.get("published")})
    return res
