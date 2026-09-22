"""试卷模板：列表、规则组卷预览、创建与删除。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models.exam import ExamDefinition, PaperTemplate
from app.models.question import DEFAULT_QUESTION_SCORE, Question
from app.models.user import User
from app.services.paper_service import generate_paper


# ---------- 管理端：试卷模板 ----------
def list_templates(db: Session) -> list[dict]:
    rows = db.execute(select(PaperTemplate).order_by(PaperTemplate.id.desc())).scalars().all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "mode": t.mode,
            "config": t.config,
            "group_ids": t.group_ids,
            "question_count": len(t.question_ids or []),
            "created_at": t.created_at,
        }
        for t in rows
    ]


def preview_paper(db: Session, config: dict, scope: set[int] | None = None) -> dict:
    """预览规则组卷结果（不落库）。

    Args:
        db: 数据库会话。
        config: 组卷规则。
        scope: 调用者数据范围分组 id；None 表示不限制（超级管理员）。
    """
    paper = generate_paper(db, config, scope)
    qids = paper["question_ids"]
    questions = []
    question_map = {q.id: q for q in db.execute(select(Question).where(Question.id.in_(qids))).scalars().all()}
    for qid in qids:
        q = question_map.get(qid)
        if q:
            questions.append(
                {
                    "id": q.id,
                    "type": q.type,
                    "question": q.question[:40],
                    "score": paper["scores"].get(qid, DEFAULT_QUESTION_SCORE),
                    "difficulty": q.difficulty,
                }
            )
    return {
        "count": paper["count"],
        "total_score": paper["total_score"],
        "question_ids": qids,
        "questions": questions,
    }


def create_template(db: Session, payload, user: User, scope: set[int] | None = None) -> dict:
    paper = generate_paper(db, payload.config, scope)
    tpl = PaperTemplate(
        name=payload.name,
        mode=payload.mode,
        config=payload.config,
        group_ids=payload.group_ids,
        question_ids=paper["question_ids"],
        created_by=user.id,
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return {"id": tpl.id, "name": tpl.name, "count": paper["count"]}


def delete_template(db: Session, template_id: int) -> None:
    """删除试卷模板；被正式考试引用时拒绝删除（保持考试可追溯）。"""
    tpl = db.get(PaperTemplate, template_id)
    if tpl is None:
        raise DomainError(status_code=404, detail="模板不存在")
    in_use = db.execute(
        select(func.count()).select_from(ExamDefinition).where(ExamDefinition.paper_template_id == template_id)
    ).scalar_one()
    if in_use:
        raise DomainError(status_code=409, detail=f"模板已被 {in_use} 场考试引用，无法删除")
    db.delete(tpl)
    db.commit()
