"""规则组卷单元测试。"""

from sqlalchemy import select

from app.database import db_session, init_db
from app.models.question import Question, QuestionBank
from app.services.paper_service import generate_paper


def _seed_questions():
    """在测试库中预置题目（幂等：已有数据则跳过）。"""
    init_db()
    with db_session() as db:
        exists = db.execute(select(Question.id).limit(1)).first()
        if exists:
            return
        bank = QuestionBank(name="测试题库")
        db.add(bank)
        db.flush()
        for i in range(10):
            db.add(
                Question(
                    bank_id=bank.id,
                    type="单选题",
                    question=f"题目{i}",
                    options=["A", "B", "C", "D"],
                    answer="A",
                    analysis="",
                    difficulty=(i % 3) + 1,
                    tags=["网络"],
                    score=2,
                )
            )
        for i in range(5):
            db.add(
                Question(
                    bank_id=bank.id,
                    type="多选题",
                    question=f"多选{i}",
                    options=["A", "B", "C"],
                    answer="AB",
                    analysis="",
                    difficulty=2,
                    tags=[],
                    score=3,
                )
            )


def test_generate_paper_by_quota():
    _seed_questions()
    with db_session() as db:
        paper = generate_paper(db, {"type_quota": {"单选题": 5, "多选题": 2}})
        assert paper["count"] == 7
        assert len(paper["question_ids"]) == 7
        assert paper["total_score"] > 0


def test_generate_paper_seed_reproducible():
    _seed_questions()
    with db_session() as db:
        p1 = generate_paper(db, {"type_quota": {"单选题": 5}, "seed": 42})
        p2 = generate_paper(db, {"type_quota": {"单选题": 5}, "seed": 42})
        assert p1["question_ids"] == p2["question_ids"]


def test_generate_paper_quota_exceeds_pool():
    _seed_questions()
    with db_session() as db:
        # 默认 max_questions=100，配额超出题库时取实际池容量。
        # 断言必须精确到 10：`<= 10` 对「组卷少出题/返回空卷」同样成立，发现不了回归。
        paper = generate_paper(db, {"type_quota": {"单选题": 100}})
        assert paper["count"] == 10


def test_generate_paper_max_questions_limit():
    _seed_questions()
    with db_session() as db:
        paper = generate_paper(db, {"type_quota": {"单选题": 5, "多选题": 5}, "max_questions": 3})
        assert paper["count"] == 3


def test_generate_paper_empty_quota():
    _seed_questions()
    with db_session() as db:
        paper = generate_paper(db, {"type_quota": {}})
        assert paper["count"] == 0
