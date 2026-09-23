"""2026-09-18 后端审查 10 项 Suggestions 的回归测试。

S1  rank() 部门数据范围（个人榜 + 分组榜）—— 排行榜已于 2026-09-23 下线，本项随之移除
S2  超管保护的原子守卫（过期预检也不能绕过）
S3  统计分组归属回退 User.dept_group_id
S4  wrong_count 按 is_correct 三态统计（未自评简答不计错）
S5  练习写库与统计刷新同事务（刷新失败整体回滚）
S6  assign_groups 校验分组存在且失败不破坏既有成员关系
S7  import_users 批量预取 + 单行失败隔离
S8  user_excel 预览按去重口令哈希 + 超长口令行级报错
S9  _check_manage_permission 在操作者缺失时 fail-closed
S10 用户列表 LIKE 转义 + 范围改用 SQL 子查询
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from io import BytesIO

import pytest
from fastapi import HTTPException
from openpyxl import Workbook

from app.core.errors import DomainError
from app.database import SessionLocal, db_session, init_db
from app.models.group import Group, UserGroup
from app.models.question import Question, QuestionBank
from app.models.record import PracticeRecord, QuestionState
from app.models.stats import StatsUserDaily
from app.models.user import User
from app.services import practice_service, stats_service, user_service
from app.utils import user_excel


def _mk_user(
    db,
    role: str = "user",
    *,
    email: str | None = None,
    name: str = "用户",
    dept_group_id: int | None = None,
) -> User:
    user = User(
        email=email or f"{secrets.token_hex(4)}@example.com",
        password_hash="x",
        name=name,
        role=role,
        status="active",
        email_verified=True,
        dept_group_id=dept_group_id,
    )
    db.add(user)
    db.flush()
    return user


def _mk_question(db, *, is_short: bool = False) -> Question:
    bank = QuestionBank(name="库", practice_enabled=True)
    db.add(bank)
    db.flush()
    q = Question(
        bank_id=bank.id,
        type="简答题" if is_short else "单选题",
        question="题",
        options=[] if is_short else ["A", "B"],
        answer="参考作答" if is_short else "A",
        analysis="",
        difficulty=1,
        tags=[],
        score=2,
    )
    db.add(q)
    db.flush()
    return q


def _today() -> str:
    return stats_service._date_str(datetime.now(timezone.utc))


# ---------- S2 ----------


def test_last_active_super_admin_is_protected():
    init_db()
    with db_session() as db:
        s1 = _mk_user(db, "super_admin")
        s2 = _mk_user(db, "super_admin")
        db.commit()

        # 两个 active 超管：可以禁用其中一个
        user_service.set_status(db, s1.id, s2.id, False, None)
        assert db.get(User, s2.id).status == "disabled"

        with pytest.raises((DomainError, HTTPException)) as disable_last:
            user_service.set_status(db, s2.id, s1.id, False, None)
        assert disable_last.value.status_code == 400

        with pytest.raises((DomainError, HTTPException)) as demote_last:
            user_service.update_user(db, s2.id, s1.id, None, "user", None, None)
        assert demote_last.value.status_code == 400

        with pytest.raises((DomainError, HTTPException)) as delete_last:
            user_service.delete_user(db, s2.id, "super_admin", s1.id, None)
        assert delete_last.value.status_code == 400

        db.expire_all()
        assert db.get(User, s1.id).role == "super_admin"
        assert db.get(User, s1.id).status == "active"


def test_atomic_guard_is_the_only_authority_for_last_super_admin():
    """最后一个 active 超管的禁用必须由条件 UPDATE 的 rowcount 拦截。

    原实现在守卫前还有一次 COUNT 预检查，但预检查在并发下可能读到过期结果，
    已删除；本用例固定「守卫本身」就是最终判据（不再依赖任何预检查）。
    """
    init_db()
    with db_session() as db:
        actor = _mk_user(db, "super_admin")
        actor.status = "disabled"  # 只有 role 参与权限判断；让 last 成为唯一 active 超管
        last = _mk_user(db, "super_admin")
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            user_service.set_status(db, actor.id, last.id, False, None)
        assert exc.value.status_code == 400
        db.expire_all()
        assert db.get(User, last.id).status == "active", "守卫必须阻止最后一个超管被禁用"


# ---------- S3 ----------


def test_stats_group_attribution_falls_back_to_dept_group_id():
    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        user = _mk_user(db, dept_group_id=group.id)  # 只有 dept_group_id，没有 UserGroup 行
        q = _mk_question(db)
        db.add(
            PracticeRecord(
                user_id=user.id,
                question_id=q.id,
                bank_id=q.bank_id,
                mode="sequence",
                user_answer="A",
                is_correct=True,
                answered_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        db.commit()

        date_str = _today()
        stats_service.refresh_daily(db, date_str)
        row = db.query(StatsUserDaily).filter(StatsUserDaily.user_id == user.id).one()
        assert row.group_id == group.id, "只有 dept_group_id 的用户必须计入其部门分组"


# ---------- S4 ----------


def test_wrong_count_excludes_pending_short_answers():
    init_db()
    with db_session() as db:
        user = _mk_user(db)
        short_q = _mk_question(db, is_short=True)
        normal_q = _mk_question(db)
        now = datetime.now(timezone.utc).isoformat()
        db.add_all(
            [
                # 简答未自评：is_correct 为 NULL，既不是对也不是错
                PracticeRecord(
                    user_id=user.id,
                    question_id=short_q.id,
                    bank_id=short_q.bank_id,
                    mode="sequence",
                    user_answer="我的作答",
                    is_correct=None,
                    answered_at=now,
                ),
                PracticeRecord(
                    user_id=user.id,
                    question_id=normal_q.id,
                    bank_id=normal_q.bank_id,
                    mode="sequence",
                    user_answer="A",
                    is_correct=False,
                    answered_at=now,
                ),
            ]
        )
        db.commit()

        stats_service.refresh_daily(db, _today())
        row = db.query(StatsUserDaily).filter(StatsUserDaily.user_id == user.id).one()
        assert row.answer_count == 1  # 未自评简答不计入已判题数
        assert row.correct_count == 0
        assert row.wrong_count == 1
        assert row.answer_count == row.correct_count + row.wrong_count


# ---------- S5 ----------


def test_practice_answer_rolls_back_when_stats_refresh_fails(monkeypatch):
    init_db()
    with db_session() as db:
        user = _mk_user(db)
        q = _mk_question(db)
        db.commit()
        user_id, question_id = user.id, q.id

    def _boom(_db, _user_id, _timestamps):
        raise RuntimeError("stats down")

    monkeypatch.setattr(stats_service, "refresh_user_for_timestamps", _boom)

    # 用独立 session 复刻 get_db 的异常回滚语义（db_session 上下文在异常被 pytest 捕获后
    # 会正常退出并 commit，不能用来验证回滚）
    with SessionLocal() as session:
        with pytest.raises(RuntimeError):
            practice_service.answer_question(session, user_id, question_id, "A", "sequence")
        session.rollback()
        # 统计刷新失败 → 练习记录/题目状态整体回滚，前端重试不会重复计分
        assert session.query(PracticeRecord).count() == 0
        assert session.query(QuestionState).count() == 0


# ---------- S6 ----------


def test_assign_groups_rejects_unknown_group_without_wiping_existing_links():
    init_db()
    with db_session() as db:
        actor = _mk_user(db, "super_admin")
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        user = _mk_user(db)
        db.add(UserGroup(user_id=user.id, group_id=group.id))
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            user_service.assign_groups(db, actor.id, user.id, [999999], None)
        assert exc.value.status_code == 400

        db.expire_all()
        links = db.query(UserGroup).filter(UserGroup.user_id == user.id).all()
        assert [link.group_id for link in links] == [group.id], "非法请求不得破坏既有分组关系"


# ---------- S7 ----------


def test_import_users_isolates_existing_email_and_ignores_unknown_group():
    init_db()
    with db_session() as db:
        actor = _mk_user(db, "super_admin")
        group = Group(name="研发部", type="部门")
        db.add(group)
        db.flush()
        existing = _mk_user(db, email="taken@example.com")
        db.commit()

        rows = [
            {"email": existing.email, "name": "重复", "role": "user", "password_hash": "x", "status": "active"},
            {
                "email": "fresh@example.com",
                "name": "新人",
                "role": "user",
                "password_hash": "x",
                "status": "active",
                "group_ids": [group.id, 999999],
            },
        ]
        res = user_service.import_users(db, actor.id, rows, scope=None, actor_role="super_admin")

        assert res["success"] == 1
        assert res["failed"] == 1
        assert [item["row"] for item in res["errors"]] == [1]
        created = db.query(User).filter(User.email == "fresh@example.com").one()
        links = db.query(UserGroup).filter(UserGroup.user_id == created.id).all()
        assert [link.group_id for link in links] == [group.id], "不存在的分组应被忽略"


# ---------- S8 ----------


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


def test_user_preview_hashes_distinct_passwords_once_and_reports_long_password():
    init_db()
    with db_session() as db:
        admin = _mk_user(db, "super_admin")
        db.commit()

        content = _user_workbook(
            [
                ["a@example.com", "甲", "普通用户", "SamePass1", "正常", ""],
                ["b@example.com", "乙", "普通用户", "SamePass1", "正常", ""],
                ["c@example.com", "丙", "普通用户", "x" * 80, "正常", ""],
            ]
        )
        preview = user_excel.preview(db, content, user_id=admin.id)
        # 超长口令是行级错误，不应让整份预览 400
        assert preview["valid_count"] == 2
        assert any("72" in item["error"] for item in preview["errors"])

        rows = user_excel.consume_preview(preview["confirm_token"], admin.id)
        assert len(rows) == 2
        assert rows[0]["password_hash"] == rows[1]["password_hash"], "相同口令应复用同一次哈希"
        assert rows[0]["password"] == ""


# ---------- S9 ----------


def test_manage_permission_fails_closed_when_actor_missing():
    init_db()
    with db_session() as db:
        target = _mk_user(db, "dept_admin")
        db.commit()

        with pytest.raises((DomainError, HTTPException)) as exc:
            user_service.set_status(db, 999999, target.id, False, None)
        assert exc.value.status_code == 403


# ---------- S10 ----------


def test_list_users_escapes_like_wildcards_and_scopes_via_subquery():
    init_db()
    with db_session() as db:
        group = Group(name="研发部", type="部门")
        other = Group(name="其他部", type="部门")
        db.add_all([group, other])
        db.flush()
        by_group = _mk_user(db, email="alice@corp.test", name="Alice")
        by_dept = _mk_user(db, email="bob@corp.test", name="Bob", dept_group_id=group.id)
        outsider = _mk_user(db, email="carol@corp.test", name="Carol")
        db.add_all(
            [
                UserGroup(user_id=by_group.id, group_id=group.id),
                UserGroup(user_id=outsider.id, group_id=other.id),
            ]
        )
        db.commit()

        # % / _ 必须按字面量匹配，不能当通配符
        assert user_service.list_users(db, 1, 100, "%", None, None, None, None)[1] == 0
        assert user_service.list_users(db, 1, 100, "_", None, None, None, None)[1] == 0
        items, total = user_service.list_users(db, 1, 100, "alice", None, None, None, None)
        assert total == 1 and items[0]["email"] == "alice@corp.test"

        # 范围过滤：user_groups 与 dept_group_id 两种归属都要命中
        scoped_items, scoped_total = user_service.list_users(db, 1, 100, None, None, None, None, {group.id})
        assert scoped_total == 2
        assert {item["id"] for item in scoped_items} == {by_group.id, by_dept.id}
