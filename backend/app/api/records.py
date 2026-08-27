"""练习路由（用户端）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.record import (
    PracticeAnswerIn,
    PracticeStartIn,
    ShortEvalIn,
    ToggleMarkIn,
)
from app.services import practice_service

router = APIRouter(prefix="/records", tags=["practice"])


@router.get("/practice/modes")
def modes(bank_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return practice_service.list_modes(db, user.id, bank_id)


@router.post("/practice/start")
def start(payload: PracticeStartIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return practice_service.start_practice(
        db,
        user.id,
        payload.mode,
        payload.type,
        payload.limit,
        payload.bank_id,
    )


@router.post("/practice/answer")
def answer(payload: PracticeAnswerIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return practice_service.answer_question(db, user.id, payload.question_id, payload.answer, payload.mode)


@router.get("/practice/progress")
def progress(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return practice_service.get_progress(db, user.id)


@router.get("/practice/recent")
def recent(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return practice_service.recent_practice(db, user.id)


@router.post("/questions/{qid}/toggle-mark")
def toggle_mark(qid: int, payload: ToggleMarkIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    practice_service.toggle_mark(db, user.id, qid, payload.marked, payload.note)
    return {"success": True}


@router.post("/questions/{qid}/short-eval")
def short_eval(qid: int, payload: ShortEvalIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    practice_service.short_eval(db, user.id, qid, payload.mastered)
    return {"success": True}
