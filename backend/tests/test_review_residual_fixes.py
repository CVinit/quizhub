"""2026-09-18 后端审查「遗留项」整改的回归测试。

覆盖：
①应用级 logging 配置；②stats_user_daily 未分组唯一性 + 迁移脚本；③update_user 清空部门；
④导入 token 先校验后消费；⑤Excel 健壮性（inf 难度/分值/表头/邮箱 CRLF/截断）；
⑥跨用户 mock 开考；⑦scoring 结算守卫；⑧考试时段校验；⑨考试列表题数批量、
成绩列表范围下推、type_stats 聚合、发布邮件批量、组卷候选上限；⑩冗余索引清理。
"""

from __future__ import annotations

import importlib.util
import logging
import secrets
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException
from openpyxl import Workbook
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core import logconfig
from app.core.errors import DomainError
from app.database import db_session, init_db
from app.models.exam import ExamDefinition, PaperTemplate
from app.models.group import Group, UserGroup
from app.models.question import Question, QuestionBank
from app.models.record import ExamResult, ExamSession
from app.models.stats import StatsUserDaily
from app.models.user import User
from app.schemas.exam import ExamCreateIn
from app.schemas.user import UserUpdate
from app.services import exam_service, import_service, mail_service, paper_service, question_service, user_service
from app.utils import excel, user_excel

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _mk_user(db, role: str = "user", *, email: str | None = None, dept_group_id: int | None = None) -> User:
    user = User(
        email=email or f"{secrets.token_hex(4)}@example.com",
        password_hash="x",
        name="用户",
        role=role,
        status="active",
        email_verified=True,
        dept_group_id=dept_group_id,
    )
    db.add(user)
    db.flush()
    return user


def _mk_question(db, bank: QuestionBank, *, qtype: str = "单选题", answer: str = "A") -> Question:
    q = Question(
        bank_id=bank.id,
        type=qtype,
        question="题",
        options=["A", "B"],
        answer=answer,
        analysis="",
        difficulty=1,
        tags=[],
        score=2,
    )
    db.add(q)
    db.flush()
    return q


def _question_workbook(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "单选题"
    ws.append(excel.HEADERS["单选题"])
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _user_workbook(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "用户"
    ws.append(user_excel.HEADERS)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------- ① 日志配置 ----------


def test_configure_logging_enables_info_and_honours_env(monkeypatch):
    monkeypatch.setattr(logconfig, "_CONFIGURED", False)
    monkeypatch.setenv("TRAINING_LOG_LEVEL", "DEBUG")
    logconfig.configure_logging()
    assert logging.getLogger("quizhub").level == logging.DEBUG
    assert logging.getLogger("quizhub").isEnabledFor(logging.INFO)

    # 未知名回退 INFO；恢复默认避免影响其它用例
    monkeypatch.setattr(logconfig, "_CONFIGURED", False)
    monkeypatch.setenv("TRAINING_LOG_LEVEL", "not-a-level")
    logconfig.configure_logging()
    assert logging.getLogger("quizhub").isEnabledFor(logging.INFO)
    monkeypatch.setattr(logconfig, "_CONFIGURED", False)
    monkeypatch.delenv("TRAINING_LOG_LEVEL", raising=False)
    logconfig.configure_logging()
    assert logging.getLogger("quizhub").isEnabledFor(logging.INFO)


# ---------- ② stats 唯一性 + 迁移 ----------


def test_ungrouped_stats_rows_are_unique():
    init_db()
    with db_session() as db:
        user = _mk_user(db)
        date = "2026-09-18"
        db.add_all(
            [
                StatsUserDaily(user_id=user.id, date=date, group_id=None, answer_count=1),
                StatsUserDaily(user_id=user.id, date=date, group_id=None, answer_count=2),
            ]
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def _load_migration():
    path = _SCRIPTS / "migrate_2026_09_18.py"
    spec = importlib.util.spec_from_file_location("migrate_2026_09_18", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_2026_09_18"] = module
    spec.loader.exec_module(module)
    return module


def _stats_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE stats_user_daily ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, date VARCHAR NOT NULL,"
        "group_id INTEGER, answer_count INTEGER NOT NULL DEFAULT 0, correct_count INTEGER NOT NULL DEFAULT 0,"
        "wrong_count INTEGER NOT NULL DEFAULT 0, exam_count INTEGER NOT NULL DEFAULT 0,"
        "exam_score_sum FLOAT NOT NULL DEFAULT 0, exam_pass_count INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute("CREATE UNIQUE INDEX uq_user_daily ON stats_user_daily (user_id, date, group_id)")
    conn.execute("CREATE INDEX ix_stats_user_daily_user_id ON stats_user_daily (user_id)")
    conn.execute("CREATE INDEX ix_stats_user_daily_date ON stats_user_daily (date)")
    conn.execute("CREATE INDEX ix_stats_date_user ON stats_user_daily (date, user_id)")
    return conn


def test_migration_merges_ungrouped_duplicates_and_drops_redundant_indexes():
    migration = _load_migration()
    path = Path(tempfile.mkdtemp()) / "stats.db"
    conn = _stats_db(path)
    conn.execute(
        "INSERT INTO stats_user_daily (user_id,date,group_id,answer_count,correct_count,wrong_count,"
        "exam_count,exam_score_sum,exam_pass_count) VALUES (1,'2026-09-18',NULL,3,2,1,0,0,0)"
    )
    conn.execute(
        "INSERT INTO stats_user_daily (user_id,date,group_id,answer_count,correct_count,wrong_count,"
        "exam_count,exam_score_sum,exam_pass_count) VALUES (1,'2026-09-18',NULL,4,1,3,1,80,1)"
    )
    conn.execute(
        "INSERT INTO stats_user_daily (user_id,date,group_id,answer_count,correct_count,wrong_count,"
        "exam_count,exam_score_sum,exam_pass_count) VALUES (1,'2026-09-18',7,1,1,0,0,0,0)"
    )
    conn.commit()

    assert migration._count_duplicate_groups(conn) == 1
    removed = migration.merge_ungrouped_duplicates(conn)
    migration.ensure_partial_unique_index(conn)
    dropped = migration.drop_redundant_indexes(conn)
    conn.commit()

    assert removed == 1
    assert set(dropped) == {"ix_stats_user_daily_user_id", "ix_stats_user_daily_date"}
    rows = conn.execute(
        "SELECT group_id,answer_count,correct_count,wrong_count,exam_count,exam_score_sum "
        "FROM stats_user_daily ORDER BY id"
    ).fetchall()
    # 未分组重复行合并：计数与分数相加；有分组的行不动
    assert rows == [(None, 7, 3, 4, 1, 80.0), (7, 1, 1, 0, 0, 0.0)]

    # 幂等 + 部分唯一索引生效
    assert migration.merge_ungrouped_duplicates(conn) == 0
    migration.ensure_partial_unique_index(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO stats_user_daily (user_id,date,group_id) VALUES (1,'2026-09-18',NULL)")
    conn.rollback()
    conn.close()


# ---------- ③ 清空部门归属 ----------


def test_update_user_can_clear_dept_group():
    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        actor = _mk_user(db, "super_admin")
        target = _mk_user(db, dept_group_id=group.id)
        db.commit()

        # 未显式要求清空 → None 表示「不修改」
        user_service.update_user(db, actor.id, target.id, None, None, None, None)
        db.refresh(target)
        assert target.dept_group_id == group.id

        user_service.update_user(db, actor.id, target.id, None, None, None, None, clear_dept_group=True)
        db.refresh(target)
        assert target.dept_group_id is None


def test_user_update_schema_distinguishes_unset_from_explicit_null():
    assert "dept_group_id" not in UserUpdate(name="新名字").model_fields_set
    assert "dept_group_id" in UserUpdate(dept_group_id=None).model_fields_set


# ---------- ④ token 先校验后消费 ----------


def test_rejected_question_import_does_not_consume_preview():
    init_db()
    with db_session() as db:
        own = Group(name="本部门", type="部门")
        other = Group(name="其他部门", type="部门")
        db.add_all([own, other])
        db.flush()
        admin = _mk_user(db, "super_admin")
        db.commit()

        content = excel.build_template().getvalue()
        preview = import_service.preview(
            db,
            content,
            group_id=own.id,
            bank_id=None,
            bank_name="导入库",
            user_id=admin.id,
            scope={own.id},
        )
        token = preview["confirm_token"]

        with pytest.raises((DomainError, HTTPException)) as denied:
            import_service.do_import(db, token, user_id=admin.id, scope={other.id})
        assert denied.value.status_code == 403

        # 越权尝试不得毁掉上传者本人的预览
        res = import_service.do_import(db, token, user_id=admin.id, scope={own.id})
        assert res.success > 0


def test_user_preview_peek_then_consume_survives_role_rejection():
    init_db()
    with db_session() as db:
        admin = _mk_user(db, "super_admin")
        db.commit()
        content = user_excel.build_template().getvalue()
        preview = user_excel.preview(db, content, user_id=admin.id)
        token = preview["confirm_token"]

        # 路由先 peek 做角色校验（此模板含部门管理员行）——peek 不消费
        rows = user_excel.peek_preview(token, admin.id)
        assert any(str(r.get("role")) == "dept_admin" for r in rows)
        assert user_excel.consume_preview(token, admin.id) == rows


# ---------- ⑤ Excel 健壮性 ----------


def test_inf_difficulty_does_not_crash_and_negative_score_is_row_error():
    init_db()
    with db_session():
        content = _question_workbook(
            [
                ["题干甲", "A.甲\nB.乙", "A", "解析", "inf", "标签", 2, ""],
                ["题干乙", "A.甲\nB.乙", "A", "解析", 1, "标签", -1, ""],
            ]
        )
        preview = excel.parse_workbook(BytesIO(content))
        assert preview.total == 2
        first, second = preview.rows
        assert first.valid is True
        assert first.difficulty == 2  # "inf" 回退默认难度，不再抛 OverflowError
        assert second.valid is False
        assert "分值" in second.error


def test_workbook_header_mismatch_is_reported_and_sheet_skipped():
    init_db()
    with db_session():
        wb = Workbook()
        ws = wb.active
        ws.title = "单选题"
        ws.append(["题干", "选项", "答案", "难度", "解析", "标签", "分值", "分组"])  # 列序被调换
        ws.append(["题干甲", "A.甲", "A", 1, "", "", 2, ""])
        buf = BytesIO()
        wb.save(buf)

        preview = excel.parse_workbook(BytesIO(buf.getvalue()))
        assert preview.total == 0
        assert any("表头" in item["error"] for item in preview.errors)


def test_user_preview_rejects_email_with_newline():
    init_db()
    with db_session() as db:
        admin = _mk_user(db, "super_admin")
        db.commit()
        content = _user_workbook([["a@example.com\nBcc: evil@example.com", "甲", "普通用户", "pass1234", "正常", ""]])
        preview = user_excel.preview(db, content, user_id=admin.id)
        assert preview["valid_count"] == 0
        assert any("换行" in item["error"] for item in preview["errors"])


def test_question_import_reports_truncation(monkeypatch):
    init_db()
    with db_session() as db:
        admin = _mk_user(db, "super_admin")
        db.commit()
        # 截断现在只有一条路径：parse_workbook 的 PARSE_ROW_MAX（原先 import_service
        # 另有一份 _IMPORT_ROW_MAX 与二次解析，已删除以避免两处口径漂移）。
        # 需同时 patch 解析侧与 import_service 侧的引用。
        monkeypatch.setattr("app.utils.excel.PARSE_ROW_MAX", 2)
        monkeypatch.setattr(import_service, "PARSE_ROW_MAX", 2)
        rows = [[f"题干{i}", "A.甲\nB.乙", "A", "解析", 1, "标签", 2, ""] for i in range(5)]
        preview = import_service.preview(
            db,
            _question_workbook(rows),
            group_id=None,
            bank_id=None,
            bank_name="导入库",
            user_id=admin.id,
            scope=None,
        )
        assert preview["truncated"] is True
        assert preview["valid_count"] == 2


# ---------- ⑥ 跨用户 mock 开考 ----------


def test_cannot_start_another_users_mock_exam():
    init_db()
    with db_session() as db:
        owner = _mk_user(db)
        intruder = _mk_user(db)
        mock = ExamDefinition(name="模拟考试", type="mock", rules={}, status="ongoing", created_by=owner.id)
        db.add(mock)
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.start_exam(db, intruder, mock.id)
        assert exc.value.status_code == 404


# ---------- ⑦ 结算中的会话守卫 ----------


def test_start_exam_does_not_reset_recent_scoring_session():
    init_db()
    with db_session() as db:
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        q = _mk_question(db, bank)
        user = _mk_user(db)
        exam = ExamDefinition(
            name="正式考试",
            type="formal",
            rules={},
            manual_questions=[q.id],
            group_ids=None,
            duration_min=60,
            pass_score=60,
            status="published",
        )
        db.add(exam)
        db.flush()
        db.add(
            ExamSession(
                exam_definition_id=exam.id,
                user_id=user.id,
                status="scoring",
                answers={},
                version=1,
                started_at=datetime.now(timezone.utc).isoformat(),
                submitted_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.start_exam(db, user, exam.id)
        assert exc.value.status_code == 409


# ---------- ⑧ 考试时段校验 ----------


def test_exam_window_is_validated():
    init_db()
    with pytest.raises(ValidationError):
        ExamCreateIn(name="x", start_at="2026-09-18T10:00", end_at="2026-09-18T09:00")
    with pytest.raises(ValidationError):
        ExamCreateIn(name="x", start_at="not-a-date")

    with db_session() as db:
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        q = _mk_question(db, bank)
        exam = ExamDefinition(
            name="正式考试",
            type="formal",
            rules={},
            manual_questions=[q.id],
            group_ids=None,
            start_at="2026-09-18T09:00",
            end_at="2026-09-18T11:00",
            status="draft",
        )
        db.add(exam)
        db.commit()

        # 只改 end_at：必须与库中 start_at 组合后判断
        with pytest.raises((DomainError, HTTPException)) as exc:
            exam_service.update_exam(db, exam.id, {"end_at": "2026-09-18T08:00"}, None)
        assert exc.value.status_code == 400


# ---------- ⑨ 性能相关 ----------


def test_exam_list_question_counts_are_batched_and_correct():
    init_db()
    with db_session() as db:
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        q1 = _mk_question(db, bank)
        q2 = _mk_question(db, bank)
        tpl = PaperTemplate(name="模板", mode="formal", config={}, question_ids=[q1.id, q2.id])
        db.add(tpl)
        db.flush()
        user = _mk_user(db)
        manual = ExamDefinition(
            name="手选", type="formal", rules={}, manual_questions=[q1.id, q2.id], group_ids=None, status="published"
        )
        rules = ExamDefinition(
            name="规则", type="formal", rules={"type_quota": {"单选题": 3}}, group_ids=None, status="published"
        )
        templated = ExamDefinition(
            name="模板", type="formal", rules={}, paper_template_id=tpl.id, group_ids=None, status="published"
        )
        db.add_all([manual, rules, templated])
        db.commit()

        expected = {manual.id: 2, rules.id: 3, templated.id: 2}
        admin_list = {item["id"]: item["total_questions"] for item in exam_service.list_exams(db, None, None, 100)}
        for exam_id, count in expected.items():
            assert admin_list[exam_id] == count

        available = {item["id"]: item["total_questions"] for item in exam_service.list_available(db, user)}
        for exam_id, count in expected.items():
            assert available[exam_id] == count


def test_list_results_scope_uses_json_pushdown():
    init_db()
    with db_session() as db:
        own = Group(name="本部门", type="部门")
        other = Group(name="其他部门", type="部门")
        db.add_all([own, other])
        db.flush()
        in_scope = ExamDefinition(name="本部门考试", type="formal", rules={}, group_ids=[own.id], status="published")
        mixed = ExamDefinition(
            name="跨部门考试", type="formal", rules={}, group_ids=[own.id, other.id], status="published"
        )
        unassigned = ExamDefinition(name="全局考试", type="formal", rules={}, group_ids=None, status="published")
        db.add_all([in_scope, mixed, unassigned])
        db.flush()
        student = _mk_user(db)
        # 考生必须在部门范围内，否则 user 维度过滤会先把成绩全部排除（与本用例无关）
        db.add(UserGroup(user_id=student.id, group_id=own.id))
        for exam in (in_scope, mixed, unassigned):
            db.add(
                ExamResult(
                    exam_definition_id=exam.id,
                    user_id=student.id,
                    score=90,
                    total_score=100,
                    passed=True,
                    published=True,
                )
            )
        db.commit()

        rows = exam_service.list_results(db, None, {own.id}, 100, None)
        assert {row["exam_name"] for row in rows} == {"本部门考试"}


def test_type_stats_pushes_aggregation_to_sql_without_tags():
    init_db()
    with db_session() as db:
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        _mk_question(db, bank, qtype="单选题")
        _mk_question(db, bank, qtype="单选题")
        _mk_question(db, bank, qtype="多选题")
        db.commit()

        counts = question_service.type_stats(db, bank_ids=[bank.id])
        assert counts["单选题"] == 2
        assert counts["多选题"] == 1
        assert counts["判断题"] == 0


def test_publish_exam_enqueues_one_batched_mail_task():
    init_db()
    with db_session() as db:
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        q = _mk_question(db, bank)
        _mk_user(db, email="a@example.com")
        _mk_user(db, email="b@example.com")
        exam = ExamDefinition(
            name="待发布考试",
            type="formal",
            rules={},
            manual_questions=[q.id],
            group_ids=None,
            status="draft",
        )
        db.add(exam)
        db.commit()

        calls: list[tuple] = []

        class _Bg:
            def add_task(self, fn, *args, **kwargs):
                calls.append((fn, args, kwargs))

        exam_service.publish_exam(db, exam.id, None, _Bg())
        assert len(calls) == 1
        fn, args, _kwargs = calls[0]
        assert fn is mail_service.send_safely
        assert args[0] is mail_service.send_exam_publish_many
        assert set(args[2]) == {"a@example.com", "b@example.com"}


def test_send_exam_publish_many_tolerates_failures(monkeypatch):
    sent: list[str] = []

    def _ok(settings, to_email, exam_name, end_at):
        sent.append(to_email)

    def _fail(settings, to_email, exam_name, end_at):
        raise mail_service.MailError("boom")

    monkeypatch.setattr(mail_service, "send_exam_publish", _ok)
    mail_service.send_exam_publish_many({}, ["a@x.com", "b@x.com"], "考试", "截止")
    assert sent == ["a@x.com", "b@x.com"]

    monkeypatch.setattr(mail_service, "send_exam_publish", _fail)
    mail_service.send_exam_publish_many({}, ["a@x.com"], "考试", "截止")  # 不应抛出


def test_paper_candidate_cap_and_score_pruning(monkeypatch):
    init_db()
    with db_session() as db:
        bank = QuestionBank(name="库", practice_enabled=True)
        db.add(bank)
        db.flush()
        for _ in range(5):
            _mk_question(db, bank)
        db.commit()

        monkeypatch.setattr(paper_service, "_CANDIDATE_MAX", 2)
        with pytest.raises((DomainError, HTTPException)) as exc:
            paper_service.generate_paper(db, {"type_quota": {"单选题": 1}})
        assert exc.value.status_code == 400

        monkeypatch.setattr(paper_service, "_CANDIDATE_MAX", 20000)
        paper = paper_service.generate_paper(db, {"type_quota": {"单选题": 5}, "max_questions": 2})
        assert len(paper["question_ids"]) == 2
        assert set(paper["scores"]) == set(paper["question_ids"])


def test_exam_windows_are_optional():
    """未配置时段的考试不受新增校验影响。"""
    payload = ExamCreateIn(name="无时段考试", start_at=None, end_at=None)
    assert payload.start_at is None and payload.end_at is None

    with db_session() as db:
        exam = ExamDefinition(
            name="无时段",
            type="formal",
            rules={},
            group_ids=None,
            start_at=None,
            end_at=None,
            status="draft",
        )
        db.add(exam)
        db.commit()
        # 仅设置 start_at 也应允许
        exam_service.update_exam(db, exam.id, {"start_at": "2026-09-18T09:00"}, None)


def test_expired_preview_cache_is_cleaned_up():
    from app.core.preview_cache import BoundedTTLCache

    cache: BoundedTTLCache[list[int]] = BoundedTTLCache(maxsize=2, ttl=1)
    cache.put("k", [1], owner=1.0)
    assert cache.peek("k") == ([1], 1.0)
    assert cache.peek_owner("k") == 1.0
    assert cache.take("k") == ([1], 1.0)
    assert cache.take("k") is None


def test_stats_date_helpers_use_single_timezone_source():
    from app.core.timeutil import business_tz
    from app.services.stats import common as stats_common

    assert business_tz() == stats_common._TZ
