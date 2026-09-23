"""2026-09-23 代码审查整改的回归测试。

覆盖本轮修复的确定性缺陷（都是「同一条约定在某条路径上漏了」）：

1. Update schema 显式 null 写入 NOT NULL 列 → 500（exam / question / group）；
2. `PUT /admin/users/{id}` 传不存在的 `dept_group_id` → 外键 IntegrityError → 500；
3. `GET /admin/question-type-stats` 超长数字参数 → `int()` ValueError → 500；
4. 用户导入「分组ID」填 `inf` / `1e400` → OverflowError 使整份导入 500；
5. 整数型系统设置用 float() 校验、消费方却 int() 解析 → 模拟开考 500；
6. 邮件模板格式错误（如 `{code!x}`）逃出 `_render` 兜底 → 整封邮件不发；
7. 复核 / 公布成绩后的统计重算未降级 → 假 500，且公布路径会跳过通知邮件。

（`refresh_user_daily` 补写锁的回归见 tests/test_stats_refresh_locking.py。）
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.database import SessionLocal, db_session, get_db, init_db
from app.main import create_app
from app.models.exam import ExamDefinition, ExamQuestion
from app.models.group import Group
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult, ExamSession, ShortAnswerReview
from app.models.user import User
from app.services import review_service, system_service


@pytest.fixture
def api(tmp_path, monkeypatch):
    """HTTP 客户端：依赖覆盖为测试库会话（与 test_api_admin_routes.py 同构）。"""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    monkeypatch.setattr("app.config.FILES_DIR", files_dir)
    monkeypatch.setattr("app.api.system.FILES_DIR", files_dir)

    app = create_app()

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client


def _mk_user(db, email: str, role: str = "user", dept_group_id: int | None = None) -> User:
    user = User(
        email=email,
        password_hash="x",
        name=email.split("@")[0],
        role=role,
        status="active",
        email_verified=True,
        dept_group_id=dept_group_id,
    )
    db.add(user)
    db.flush()
    return user


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, {'ver': user.token_version})}"}


def _seed_core() -> dict:
    """建一个分组 + 超管 + 题库 + 题目 + 考试 + 普通用户，返回 id 与鉴权头。"""
    db = SessionLocal()
    try:
        group = Group(name="总部", type="部门")
        db.add(group)
        db.commit()
        db.refresh(group)

        admin = _mk_user(db, "boss@example.com", "super_admin", dept_group_id=group.id)
        bank = QuestionBank(name="题库A", group_id=group.id)
        db.add(bank)
        db.flush()
        question = Question(
            bank_id=bank.id,
            type="单选题",
            question="1+1=?",
            options=["1", "2"],
            answer="B",
            analysis="",
            difficulty=2,
            score=2.0,
            group_id=group.id,
        )
        db.add(question)
        db.flush()
        exam = ExamDefinition(
            name="考试A",
            type="formal",
            rules={},
            group_ids=[group.id],
            status="draft",
            created_by=admin.id,
        )
        db.add(exam)
        db.flush()
        target = _mk_user(db, "t@example.com", "user", dept_group_id=group.id)
        db.commit()
        return {
            "headers": _headers(admin),
            "group": group.id,
            "question": question.id,
            "exam": exam.id,
            "user": target.id,
        }
    finally:
        db.close()


# ---------- 1. 显式 null → 422（原先 500） ----------
@pytest.mark.parametrize(
    "body",
    [
        {"name": None},
        {"show_score_immediately": None},
        {"show_analysis": None},
        {"need_review": None},
        {"duration_min": None},
    ],
)
def test_exam_update_explicit_null_is_422(api, body):
    ids = _seed_core()
    resp = api.put(f"/api/admin/exams/{ids['exam']}", json=body, headers=ids["headers"])
    assert resp.status_code == 422, resp.text


def test_exam_update_nullable_field_still_accepts_null(api):
    """对照：可空列仍允许显式 null，PATCH 语义没有被过度收紧。"""
    ids = _seed_core()
    resp = api.put(f"/api/admin/exams/{ids['exam']}", json={"start_at": None}, headers=ids["headers"])
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("body", [{"question": None}, {"analysis": None}, {"difficulty": None}, {"score": None}])
def test_question_update_explicit_null_is_422(api, body):
    ids = _seed_core()
    resp = api.put(f"/api/admin/questions/{ids['question']}", json=body, headers=ids["headers"])
    assert resp.status_code == 422, resp.text


def test_question_update_nullable_field_still_accepts_null(api):
    """对照：tags 等可空列显式 null 仍合法（校验不能误伤 PATCH 语义）。"""
    ids = _seed_core()
    resp = api.put(f"/api/admin/questions/{ids['question']}", json={"tags": None}, headers=ids["headers"])
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("body", [{"name": None}, {"sort": None}])
def test_group_update_explicit_null_is_422(api, body):
    ids = _seed_core()
    resp = api.put(f"/api/admin/groups/{ids['group']}", json=body, headers=ids["headers"])
    assert resp.status_code == 422, resp.text


def test_group_update_parent_id_null_still_allowed(api):
    """对照：parent_id 为可空列，显式 null 表示「移到根」，语义合法。"""
    ids = _seed_core()
    resp = api.put(f"/api/admin/groups/{ids['group']}", json={"parent_id": None}, headers=ids["headers"])
    assert resp.status_code == 200, resp.text


# ---------- 2. 不存在的 dept_group_id → 400（原先外键 IntegrityError 500） ----------
def test_update_user_unknown_dept_group_is_400(api):
    ids = _seed_core()
    resp = api.put(f"/api/admin/users/{ids['user']}", json={"dept_group_id": 999999}, headers=ids["headers"])
    assert resp.status_code == 400, resp.text


def test_update_user_existing_dept_group_still_ok(api):
    """对照：合法分组仍可写入（校验不能误伤正常路径）。"""
    ids = _seed_core()
    resp = api.put(f"/api/admin/users/{ids['user']}", json={"dept_group_id": ids["group"]}, headers=ids["headers"])
    assert resp.status_code == 200, resp.text


# ---------- 3. 超长数字查询参数 → 200（原先 int() ValueError 500） ----------
def test_question_type_stats_ignores_overlong_digits(api):
    ids = _seed_core()
    resp = api.get(
        "/api/admin/question-type-stats",
        params={"bank_ids": "1" * 5000, "group_ids": "2" * 5000},
        headers=ids["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert set(resp.json()) == {"单选题", "多选题", "判断题", "填空题", "简答题", "拖拽题"}


# ---------- 4. 分组ID 非有限数字不再炸整份导入 ----------
def test_parse_group_ids_skips_non_finite():
    from app.utils.user_excel import _parse_group_ids

    assert _parse_group_ids("inf,1e400,3") == [3]
    assert _parse_group_ids("1，2") == [1, 2]
    assert _parse_group_ids("abc,0,-1") == []


# ---------- 5. 整数型设置校验口径与消费方一致 ----------
def test_integer_settings_reject_decimal():
    """`default_exam_duration_min` 会被 exam/mock.py 用 int() 解析：小数必须在校验期拒绝。"""
    init_db()
    with db_session() as db:
        with pytest.raises(ValueError):
            system_service.update_settings(db, "exam", {"default_exam_duration_min": "90.5"})
        with pytest.raises(ValueError):
            system_service.update_settings(db, "exam", {"default_exam_duration_min": "0"})

        system_service.update_settings(db, "exam", {"default_exam_duration_min": "90"})
        assert system_service.get_settings(db, "exam")["default_exam_duration_min"] == "90"


def test_upload_size_setting_still_accepts_decimal():
    """对照：upload_max_size_mb 的消费方用 float()，小数 MB 仍应被接受。"""
    init_db()
    with db_session() as db:
        system_service.update_settings(db, "upload", {"upload_max_size_mb": "10.5"})
        assert system_service.get_settings(db, "upload")["upload_max_size_mb"] == "10.5"


# ---------- 6. 邮件模板异常必须回退默认文案（而非丢弃整封邮件） ----------
@pytest.mark.parametrize("tpl", ["{code!x}", "{code:02d}", "{code.missing}"])
def test_render_falls_back_on_bad_template(tpl):
    from app.services.mail_service import _render

    assert _render(tpl, code="123") == ""


def test_render_keeps_valid_template():
    from app.services.mail_service import _render

    assert _render("验证码：{code}", code="123") == "验证码：123"


# ---------- 7. 复核 / 公布：统计重算失败不得变成 500 ----------
def _seed_review(db) -> tuple[User, ExamDefinition, ExamResult, list[ShortAnswerReview]]:
    """建一场含 1 道简答的考试 + 待复核记录（同一会话内 flush）。"""
    now = datetime.now(timezone.utc).isoformat()
    admin = _mk_user(db, f"admin-{secrets.token_hex(3)}@example.com", "super_admin")
    student = _mk_user(db, f"stu-{secrets.token_hex(3)}@example.com")
    bank = QuestionBank(name="简答库", practice_enabled=True)
    db.add(bank)
    db.flush()

    exam = ExamDefinition(
        name="复核考试",
        type="formal",
        rules={},
        group_ids=None,
        duration_min=60,
        pass_score=60.0,
        max_attempts=0,
        show_score_immediately=True,
        show_analysis=False,
        need_review=True,
        status="reviewing",
        created_by=admin.id,
    )
    db.add(exam)
    db.flush()

    session = ExamSession(
        exam_definition_id=exam.id,
        user_id=student.id,
        status="scoring",
        answers={},
        version=1,
        started_at=now,
        submitted_at=now,
    )
    db.add(session)
    db.flush()

    result = ExamResult(
        exam_definition_id=exam.id,
        user_id=student.id,
        exam_session_id=session.id,
        score=0,
        total_score=100.0,
        passed=False,
        published=False,
        need_review=True,
    )
    db.add(result)
    db.flush()

    question = Question(
        bank_id=bank.id,
        type="简答题",
        question="简答1",
        options=None,
        answer="参考答案",
        analysis="",
        difficulty=1,
        tags=[],
        score=10.0,
    )
    db.add(question)
    db.flush()
    db.add(ExamQuestion(exam_definition_id=exam.id, question_id=question.id, seq=0, score=10.0))

    review = ShortAnswerReview(
        exam_result_id=result.id,
        exam_session_id=session.id,
        user_id=student.id,
        question_id=question.id,
        user_answer="我的作答",
        reference_answer="参考答案",
    )
    db.add(review)
    db.flush()
    return admin, exam, result, [review]


def _boom(*_args, **_kwargs):
    raise RuntimeError("database is locked")


def test_review_survives_stats_refresh_failure(monkeypatch):
    """统计重算失败不得把已落库的复核变成 500（否则重试只会得到「该题已复核」）。"""
    init_db()
    with db_session() as db:
        admin, _exam, _result, reviews = _seed_review(db)
        db.commit()
        review_id = reviews[0].id

        monkeypatch.setattr("app.services.stats_service.refresh_user_for_timestamps", _boom)
        out = review_service.review(db, review_id, "pass", None, admin, None)

        assert out == {"success": True, "verdict": "pass"}
        # 用新会话确认已落库：本会话的对象可能仍是更新前的快照（expire_on_commit=False）
        with SessionLocal() as verify:
            row = verify.get(ShortAnswerReview, review_id)
            assert row is not None
            assert row.verdict == "pass"


def test_publish_results_still_queues_mail_when_refresh_fails(monkeypatch):
    """公布后统计重算失败不得跳过通知邮件（重试时已无 published=False 的行）。"""
    init_db()
    with db_session() as db:
        admin, exam, _result, reviews = _seed_review(db)
        db.commit()
        review_id, exam_id = reviews[0].id, exam.id

        review_service.review(db, review_id, "pass", None, admin, None)

        monkeypatch.setattr("app.services.stats_service.refresh_for_timestamps", _boom)
        queued: list[tuple] = []

        class _BG:
            def add_task(self, fn, *args):  # noqa: ANN002, ANN003
                queued.append((fn, args))

        out = review_service.publish_results(db, exam_id, None, _BG())

        assert out["published"] == 1
        assert queued, "统计重算失败不应跳过成绩公布通知邮件"
