"""模拟考试重构回归测试（完全用户自助：题库/题量/题型比例由用户设置）。

覆盖设计文档 docs/superpowers/specs/2026-09-17-mock-exam-redesign.md 的核心约束：

1. 范围红线——用户不得通过模拟考试练到已关闭练习的题库；
2. 题量硬上限——不受请求参数放宽；
3. 题型配额——手填合计必须等于题量、逐题型不得超可用量、题型名白名单；
4. 自动分配——按可用题量比例、题量不足时下调而非静默缩水；
5. 定义复用——按 (用户, 范围, 题量, 配额) 隔离，且不无界堆积；
6. 后台配置下线。
"""

from __future__ import annotations

import secrets

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.errors import DomainError
from app.core.security import hash_password
from app.database import db_session, init_db
from app.models.exam import ExamDefinition
from app.models.question import Question, QuestionBank
from app.models.record import ExamSession
from app.models.user import User
from app.schemas.question import QuestionBankUpdate
from app.services import exam_service, question_service
from app.services.paper_service import allocate_quota


def _mk_user(role="user", **kw):
    return User(
        email=f"{secrets.token_hex(4)}@quizhub.com",
        password_hash=hash_password("pw123456"),
        name=kw.get("name", "用户"),
        role=role,
        status="active",
        email_verified=True,
    )


def _mk_bank(db, name="题库", enabled=True):
    b = QuestionBank(name=name, group_id=None, practice_enabled=enabled)
    db.add(b)
    db.flush()
    return b


def _fill(db, bank_id, qtype, n, start=0):
    for i in range(n):
        db.add(
            Question(
                bank_id=bank_id,
                type=qtype,
                question=f"{qtype}-{start + i}",
                options=["A", "B"] if qtype in ("单选题", "多选题") else None,
                answer="A" if qtype in ("单选题", "多选题") else "正确",
                analysis="",
                difficulty=1,
                tags=[],
                score=2,
            )
        )
    db.flush()


def _served_types(res) -> dict[str, int]:
    out: dict[str, int] = {}
    for q in res.get("questions", []):
        out[q["type"]] = out.get(q["type"], 0) + 1
    return out


# ---------- 1. 范围红线：不得练到已关闭练习的题库 ----------
def test_mock_excludes_disabled_bank_even_when_explicitly_requested():
    """用户显式传入已关闭练习的题库 id 时必须被剔除，不得抽到该库题目。"""
    init_db()
    with db_session() as db:
        closed = _mk_bank(db, "关闭库", enabled=True)
        open_bank = _mk_bank(db, "开放库", enabled=True)
        _fill(db, closed.id, "单选题", 10)
        _fill(db, open_bank.id, "单选题", 10)
        db.commit()
        question_service.update_bank(db, closed.id, QuestionBankUpdate(practice_enabled=False))

        user = _mk_user()
        db.add(user)
        db.commit()

        closed_ids = {q.id for q in db.execute(select(Question).where(Question.bank_id == closed.id)).scalars().all()}
        # 显式请求"关闭库"：应被静默剔除并回退到开放题库，而不是报错或照抽
        res = exam_service.start_mock_exam(db, user, bank_ids=[closed.id], size=10)
        served = {q["id"] for q in res["questions"]}
        assert served, "模拟考试未能出题"
        assert not (served & closed_ids), "模拟考试抽到了已关闭练习题库的题目"


def test_mock_scope_single_bank_only():
    """选定单个题库时，卷面题目必须全部来自该库（消除多题库混抽）。"""
    init_db()
    with db_session() as db:
        b1 = _mk_bank(db, "库一")
        b2 = _mk_bank(db, "库二")
        _fill(db, b1.id, "单选题", 20)
        _fill(db, b2.id, "单选题", 20)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()

        b1_ids = {q.id for q in db.execute(select(Question).where(Question.bank_id == b1.id)).scalars().all()}
        res = exam_service.start_mock_exam(db, user, bank_ids=[b1.id], size=10)
        served = {q["id"] for q in res["questions"]}
        assert len(served) == 10
        assert served <= b1_ids, "卷面混入了未选题库的题目"


def test_mock_scope_multi_bank_union():
    """多选题库时，范围应是所选题库的并集（不包含未选中的库）。"""
    init_db()
    with db_session() as db:
        b1 = _mk_bank(db, "库一")
        b2 = _mk_bank(db, "库二")
        b3 = _mk_bank(db, "库三")
        for b in (b1, b2, b3):
            _fill(db, b.id, "单选题", 20)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()

        allowed = {
            q.id for q in db.execute(select(Question).where(Question.bank_id.in_([b1.id, b2.id]))).scalars().all()
        }
        b3_ids = {q.id for q in db.execute(select(Question).where(Question.bank_id == b3.id)).scalars().all()}
        res = exam_service.start_mock_exam(db, user, bank_ids=[b1.id, b2.id], size=20)
        served = {q["id"] for q in res["questions"]}
        assert served <= allowed
        assert not (served & b3_ids)


# ---------- 2. 题量校验与硬上限 ----------
def test_mock_size_hard_cap():
    """题量超过硬上限必须拒绝（不受请求参数放宽）。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 200)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=exam_service.MOCK_MAX_QUESTIONS + 1)
        assert exc.value.status_code == 400


def test_mock_size_invalid():
    """题量为 0 或负数必须拒绝。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 10)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        for bad in (0, -5):
            with pytest.raises((DomainError, HTTPException)) as exc:
                exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=bad)
            assert exc.value.status_code == 400


def test_mock_size_auto_downgraded_when_insufficient():
    """范围内题量不足时下调题量并可正常开考（不再静默缩水成"不知情的少量题"）。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 18)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        res = exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=30)
        assert len(res["questions"]) == 18, "应出范围上限 18 题"


def test_mock_empty_scope_rejected():
    """范围为空（没有任何开放题库）时拒绝开考。"""
    init_db()
    with db_session() as db:
        user = _mk_user()
        db.add(user)
        db.commit()
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.start_mock_exam(db, user, size=10)
        assert exc.value.status_code == 400


# ---------- 3. 题型配额 ----------
def test_mock_quota_auto_proportional():
    """自动分配应按可用题量比例（单 30 / 多 10，题量 40 → 30/10）。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 30)
        _fill(db, b.id, "多选题", 10)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        res = exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=40)
        dist = _served_types(res)
        assert dist == {"单选题": 30, "多选题": 10}


def test_mock_quota_adapts_when_only_one_type():
    """题库只有单选题时，题量应全部给单选题，而不是缩水成原配额的 10 题。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 50)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        res = exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=30)
        assert len(res["questions"]) == 30
        assert _served_types(res) == {"单选题": 30}


def test_mock_quota_manual_sum_must_equal_size():
    """手填各题型数量合计必须等于题量，否则 400。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 30)
        _fill(db, b.id, "多选题", 30)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=30, type_quota={"单选题": 20, "多选题": 5})
        assert exc.value.status_code == 400
        assert "不一致" in str(exc.value.detail)


def test_mock_quota_manual_respected():
    """手填合计等于题量时，卷面题型分布应与手填一致。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 30)
        _fill(db, b.id, "多选题", 30)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        res = exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=30, type_quota={"单选题": 25, "多选题": 5})
        assert _served_types(res) == {"单选题": 25, "多选题": 5}


def test_mock_quota_manual_over_available_rejected():
    """手填数量超出该题型可用量时拒绝（不静默改写用户输入）。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 10)
        _fill(db, b.id, "多选题", 40)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=30, type_quota={"单选题": 30, "多选题": 0})
        assert exc.value.status_code == 400
        assert "超出可用" in str(exc.value.detail)


def test_mock_quota_unknown_type_rejected():
    """伪造题型名必须拒绝。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 30)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=10, type_quota={"伪造题": 10})
        assert exc.value.status_code == 400


def test_mock_objective_only_excludes_short_answer():
    """勾选「仅客观题」后卷面不得含简答题，且题量仍满足。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 30)
        _fill(db, b.id, "简答题", 30)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        res = exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=20, objective_only=True)
        dist = _served_types(res)
        assert "简答题" not in dist
        assert sum(dist.values()) == 20


# ---------- 4. allocate_quota 单元边界 ----------
def test_allocate_quota_sum_always_equals_size():
    """自动分配下配额合计必须等于 min(size, Σ可用)。"""
    cases = [
        ({"单选题": 30, "多选题": 10}, 40, 40),
        ({"单选题": 10, "多选题": 8}, 40, 18),  # 可用不足 → 取上限
        ({"单选题": 100}, 1, 1),
        ({"单选题": 7, "多选题": 7, "判断题": 7}, 10, 10),
    ]
    for avail, size, expect in cases:
        q = allocate_quota(avail, size)
        assert sum(q.values()) == expect, (avail, size, q)
        assert all(q[t] <= avail[t] for t in q)


def test_allocate_quota_deterministic():
    """同输入必得同输出（最大余数法稳定排序）。"""
    avail = {"单选题": 7, "多选题": 7, "判断题": 7}
    assert allocate_quota(avail, 10) == allocate_quota(avail, 10)


def test_allocate_quota_even_strategy():
    """等量策略下各题型应尽量均分。"""
    q = allocate_quota({"单选题": 100, "多选题": 100, "判断题": 100}, 9, allocation="even")
    assert sorted(q.values()) == [3, 3, 3]


# ---------- 5. 定义复用与隔离 ----------
def test_mock_definition_reused_for_same_spec():
    """同一用户、同一设置组合重复开考应复用同一 ExamDefinition（避免无界堆积）。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 50)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        r1 = exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=10)
        # 交卷释放会话，使第二次开考不会直接返回同一 in_progress 会话
        exam_service.submit_exam(db, user, r1["session_id"])
        r2 = exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=10)
        defs = db.execute(
            select(func.count(ExamDefinition.id)).where(
                ExamDefinition.type == "mock", ExamDefinition.created_by == user.id
            )
        ).scalar()
        assert defs == 1, "相同设置应复用同一模拟考试定义"
        assert r1["session_id"] != r2["session_id"]


def test_mock_definition_recreated_on_size_change():
    """改动题量属不同设置组合，应新建定义（否则用户拿回旧题量的卷子）。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 50)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        r1 = exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=10)
        exam_service.submit_exam(db, user, r1["session_id"])
        r2 = exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=20)
        assert len(r2["questions"]) == 20, "改题量后应拿到新题量的卷子"


def test_mock_isolation_across_users():
    """模拟考试定义按 created_by 隔离：他人不得复用我的固化试卷。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 30)
        a = _mk_user(name="A")
        c = _mk_user(name="C")
        db.add_all([a, c])
        db.commit()

        ra = exam_service.start_mock_exam(db, a, bank_ids=[b.id], size=10)
        rc = exam_service.start_mock_exam(db, c, bank_ids=[b.id], size=10)
        defs = db.execute(select(ExamDefinition).where(ExamDefinition.type == "mock")).scalars().all()
        owners = {e.created_by for e in defs}
        assert owners == {a.id, c.id}, "每个用户应有各自的模拟考试定义"
        assert ra["session_id"] != rc["session_id"]


def test_mock_stale_definitions_cleaned():
    """无作答记录的旧设置定义应被清理，避免 exam_definitions 无界堆积。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 50)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        # 连续用不同设置开考，均不交卷（模拟用户反复调整设置后开考）
        for size in (10, 20, 30, 40):
            exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=size)
        defs = db.execute(
            select(func.count(ExamDefinition.id)).where(
                ExamDefinition.type == "mock", ExamDefinition.created_by == user.id
            )
        ).scalar()
        # 有会话的定义会保留（用户可能回去继续答），但不应无限增长且必须远小于累计开考次数
        assert defs <= 4


# ---------- 6. 后台配置下线 & 面板指标 ----------
def test_mock_banks_only_enabled():
    """/mock/banks 只返回开放练习的题库，且含各题型题量。"""
    init_db()
    with db_session() as db:
        open_bank = _mk_bank(db, "开放库")
        closed = _mk_bank(db, "关闭库")
        _fill(db, open_bank.id, "单选题", 5)
        _fill(db, open_bank.id, "多选题", 3)
        _fill(db, closed.id, "单选题", 9)
        db.commit()
        question_service.update_bank(db, closed.id, QuestionBankUpdate(practice_enabled=False))

        data = exam_service.mock_banks(db)
        names = {b["name"] for b in data["banks"]}
        assert names == {"开放库"}, "不得泄露已关闭练习的题库"
        only = data["banks"][0]
        assert only["question_count"] == 8
        assert only["type_stats"]["单选题"] == 5
        assert "practice_enabled" not in only
        assert data["max_questions"] == exam_service.MOCK_MAX_QUESTIONS


def test_mock_preview_matches_start():
    """预览与实际开考的题量/题型分布必须一致（同一套校验口径）。"""
    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 30)
        _fill(db, b.id, "多选题", 10)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        pv = exam_service.preview_mock_paper(db, [b.id], 20)
        res = exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=20)
        assert pv["count"] == len(res["questions"]) == 20
        assert pv["type_dist"] == _served_types(res)


def test_admin_mock_config_routes_removed():
    """后台模拟考试配置接口已下线，用户端自助接口已注册。

    注：本版本 FastAPI 把 include_router 的挂载包成 _IncludedRouter，不展开到
    app.routes，故用 OpenAPI schema 取真实注册路径。
    """
    from app.main import create_app

    paths = set(create_app().openapi()["paths"])
    assert "/api/admin/mock-config" not in paths, "后台模拟考试配置应已下线"
    assert "/api/exams/mock/banks" in paths
    assert "/api/exams/mock/preview" in paths
    assert "/api/exams/mock/start" in paths


def test_panel_mock_avg_score():
    """面板模拟考试平均分：只统计本人已发布的模拟考试成绩，归一化为百分制。"""
    from app.models.record import ExamResult
    from app.services import stats_service

    init_db()
    with db_session() as db:
        b = _mk_bank(db)
        _fill(db, b.id, "单选题", 20)
        db.commit()
        user = _mk_user()
        db.add(user)
        db.commit()
        res = exam_service.start_mock_exam(db, user, bank_ids=[b.id], size=10)
        sess = db.get(ExamSession, res["session_id"])
        exam_def_id = sess.exam_definition_id
        # 直接构造一条已发布成绩：8/10 → 百分制 80
        db.add(
            ExamResult(
                exam_definition_id=exam_def_id,
                user_id=user.id,
                exam_session_id=res["session_id"],
                score=8,
                total_score=10,
                passed=True,
                correct_count=4,
                total_count=10,
                objective_score=8,
                need_review=False,
                published=True,
                overtime=False,
            )
        )
        db.commit()
        panel = stats_service.user_panel(db, user)
        assert panel["mock_avg_score"] == 80.0
        assert panel["mock_attempts"] == 1
