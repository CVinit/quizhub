"""2026-09-24 全面审查：补齐 4 处零覆盖路径的用例。

覆盖率报告（`pytest --cov=app`）显示下列分支此前**从未被执行**，即功能与安全控制只有实现、
没有回归网：

1. `paper_service.generate_paper` 的「难度配比」分层抽样分支（含余数补给、某难度池不足时的
   全池回补）——「难度配比」是模板/考试组卷的正式功能；
2. `system_service._reject_internal_host` 的拒绝分支（SMTP 主机 SSRF 加固）；
3. `GET /api/system/settings` 的敏感值掩码逻辑（测试库 settings 表为空，循环体一次都没进过）；
4. `system_service.ensure_defaults`（只在 init_db / 容器启动时跑）。
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.core import security
from app.core.errors import DomainError
from app.core.security import create_access_token
from app.database import SessionLocal, db_session, get_db, init_db
from app.main import create_app
from app.models.question import Question, QuestionBank
from app.models.system import Setting
from app.models.user import User
from app.services import paper_service, system_service
from app.services.system_service import get_settings


@pytest.fixture
def api(tmp_path, monkeypatch):
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


@pytest.fixture
def fernet(monkeypatch):
    """固定一个 Fernet 实例：测试库默认不配置 TRAINING_ENC_KEY（见 conftest）。"""
    instance = Fernet(Fernet.generate_key())
    monkeypatch.setattr(security, "_fernet", lambda: instance)
    return instance


def _seed_admin() -> dict[str, str]:
    with SessionLocal() as db:
        admin = User(
            email="root@example.com",
            password_hash="x",
            name="超管",
            role="super_admin",
            status="active",
            email_verified=True,
        )
        db.add(admin)
        db.commit()
        token = create_access_token(admin.id, {"role": admin.role, "ver": admin.token_version})
    return {"Authorization": f"Bearer {token}"}


# ---------- 1. 难度配比分层抽样 ----------


def _seed_questions(*, difficulty_counts: dict[int, int]) -> dict[int, list[int]]:
    """按难度造题，返回 {难度: [题目 id]}。"""
    out: dict[int, list[int]] = {}
    with db_session() as db:
        bank = QuestionBank(name="题库")
        db.add(bank)
        db.flush()
        for difficulty, count in difficulty_counts.items():
            out[difficulty] = []
            for i in range(count):
                q = Question(
                    bank_id=bank.id,
                    type="单选题",
                    question=f"难度{difficulty}-{i}",
                    options=["甲", "乙"],
                    answer="A",
                    difficulty=difficulty,
                    score=2.0,
                )
                db.add(q)
                db.flush()
                out[difficulty].append(q.id)
    return out


def test_generate_paper_honours_difficulty_distribution():
    """难度配比生效：4 题按 1:1 分到难度 1/2，各抽 2 题。"""
    init_db()
    ids = _seed_questions(difficulty_counts={1: 3, 2: 3})

    with db_session() as db:
        paper = paper_service.generate_paper(db, {"type_quota": {"单选题": 4}, "difficulty_dist": {"1": 0.5, "2": 0.5}})

    assert paper["count"] == 4
    picked = set(paper["question_ids"])
    assert len(picked & set(ids[1])) == 2
    assert len(picked & set(ids[2])) == 2


def test_generate_paper_gives_remainder_to_first_difficulty():
    """余数补给首个难度：3 题按 0.34/0.33/0.33 取整后余 2 个名额，全部补到难度 1。"""
    init_db()
    ids = _seed_questions(difficulty_counts={1: 3, 2: 3})

    with db_session() as db:
        paper = paper_service.generate_paper(
            db,
            {"type_quota": {"单选题": 3}, "difficulty_dist": {"1": 0.34, "2": 0.33, "3": 0.33}},
        )

    assert paper["count"] == 3
    picked = set(paper["question_ids"])
    assert len(picked & set(ids[1])) == 3, "余数补给后应全部来自难度 1"


def test_generate_paper_tops_up_from_full_pool_when_difficulty_short():
    """指定难度池不足时从该题型全池补抽（否则会静默出少于配额的题）。"""
    init_db()
    ids = _seed_questions(difficulty_counts={1: 5})

    with db_session() as db:
        paper = paper_service.generate_paper(db, {"type_quota": {"单选题": 4}, "difficulty_dist": {"3": 1.0}})

    assert paper["count"] == 4, "难度 3 一题没有，也必须按全池补足配额"
    assert set(paper["question_ids"]).issubset(set(ids[1]))


@pytest.mark.parametrize(
    "config",
    [
        {"type_quota": {"未知题型": 1}},  # 未知题型名：配额恒为空
        {"difficulty_dist": {"4": 0.5}},  # 难度只能是 1~3
        {"difficulty_dist": {"1": 1.5}},  # 比例不能超过 1
        {"difficulty_dist": {"1": 0.6, "2": 0.6}},  # 合计不能超过 1
        {"type_quota": {"单选题": 5}, "order_mode": "???"},
    ],
)
def test_validate_config_rejects_bad_shapes(config):
    """`validate_config` 是入口校验的唯一实现，必须覆盖各类畸形结构。"""
    init_db()
    _seed_questions(difficulty_counts={1: 3})

    with db_session() as db, pytest.raises(DomainError) as exc:
        paper_service.generate_paper(db, config)

    assert exc.value.status_code == 400


# ---------- 2. SMTP 主机 SSRF 加固 ----------


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "169.254.169.254", "0.0.0.0"])
def test_smtp_host_rejects_internal_targets(api, host):
    """回环/链路本地/未指定地址必须被拒绝（云元数据 169.254.169.254 是主要靶点）。"""
    init_db()
    headers = _seed_admin()

    resp = api.put(
        "/api/system/settings",
        headers=headers,
        json={"category": "smtp", "updates": {"smtp_host": host}},
    )

    assert resp.status_code == 400, resp.text
    assert "SMTP 服务器" in resp.json()["detail"]


def test_smtp_host_allows_private_relay():
    """内网中继（10/172/192 段）必须放行：一刀切会破坏企业内网部署。"""
    init_db()
    with db_session() as db:
        system_service.update_settings(db, "smtp", {"smtp_host": "10.0.0.5"})
        assert get_settings(db, "smtp")["smtp_host"] == "10.0.0.5"


def test_reject_internal_host_tolerates_unresolvable_name():
    """解析失败（离线/DNS 不可用）不得阻断配置：真正的连接失败由 mail_service 回显。"""
    system_service._reject_internal_host("no-such-host.invalid")


# ---------- 3. 设置接口的敏感值掩码 ----------


def test_settings_list_masks_encrypted_values(api, fernet):
    """列表接口对加密项只回占位符：绝不回传 SMTP 密码明文。"""
    init_db()
    headers = _seed_admin()

    written = api.put(
        "/api/system/settings",
        headers=headers,
        json={"category": "smtp", "updates": {"smtp_password": "plain-secret"}},
    )
    assert written.status_code == 200, written.text

    resp = api.get("/api/system/settings", headers=headers, params={"category": "smtp"})
    assert resp.status_code == 200, resp.text
    items = {item["key"]: item for item in resp.json()}
    assert items["smtp_password"]["value"] == security.MASKED_SECRET
    assert items["smtp_password"]["encrypted"] is True
    assert "plain-secret" not in resp.text

    # 不带 category 的分支同样要掩码（两个分支共用同一常量）
    resp_all = api.get("/api/system/settings", headers=headers)
    all_items = {item["key"]: item for item in resp_all.json()}
    assert all_items["smtp_password"]["value"] == security.MASKED_SECRET
    assert "plain-secret" not in resp_all.text

    # 库里存的必须是密文，且回传的掩码不会被当作新值写回
    with db_session() as db:
        row = db.execute(select(Setting).where(Setting.setting_key == "smtp_password")).scalar_one()
        assert row.value.startswith("enc:")
        assert row.value != "plain-secret"
    assert (
        api.put(
            "/api/system/settings",
            headers=headers,
            json={"category": "smtp", "updates": {"smtp_password": security.MASKED_SECRET}},
        ).status_code
        == 200
    )
    with db_session() as db:
        assert get_settings(db, "smtp")["smtp_password"] == "plain-secret", "掩码不得覆盖真实密文"


# ---------- 4. ensure_defaults ----------


def test_ensure_defaults_is_idempotent_and_keeps_existing_values():
    """默认设置只补缺失项：重复调用不重复插入、也不覆盖管理员已改的值。"""
    init_db()
    with db_session() as db:
        system_service.ensure_defaults(db)
        first = db.execute(select(func.count()).select_from(Setting)).scalar_one()
        system_service.ensure_defaults(db)
        second = db.execute(select(func.count()).select_from(Setting)).scalar_one()

    assert first == second == len(system_service.DEFAULT_SETTINGS)

    with db_session() as db:
        db.execute(update(Setting).where(Setting.setting_key == "site_name").values(value="自定义站点"))
        db.commit()

    with db_session() as db:
        system_service.ensure_defaults(db)
        assert get_settings(db, "general")["site_name"] == "自定义站点"
