"""进程内缓存与邮件发送的单元测试。

`preview_cache`（36%）与 `mail_service`（44%）此前主要被间接使用，
这里直接覆盖容量上限、TTL、单次消费、鉴权归属，以及 SMTP 的 SSL/STARTTLS/
匿名中继/错误包装各分支——这些分支一旦走错，管理员会看到"已发送"却收不到邮件。
"""

from __future__ import annotations

import logging
import smtplib

import pytest

from app.core.preview_cache import BoundedTTLCache
from app.services import mail_service


class _FakeTime:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def fake_time(monkeypatch):
    fake = _FakeTime()
    monkeypatch.setattr("app.core.preview_cache.time", fake)
    return fake


# ---------- BoundedTTLCache ----------
def test_cache_put_take_single_consume_with_owner(fake_time):
    cache: BoundedTTLCache[dict] = BoundedTTLCache(maxsize=4, ttl=10)

    cache.put("token", {"rows": [1, 2]}, owner=7.0)

    assert len(cache) == 1
    assert cache.peek_owner("token") == 7.0
    assert cache.take("token") == ({"rows": [1, 2]}, 7.0)
    # 单次消费：第二次取出为 None
    assert cache.take("token") is None
    assert len(cache) == 0


def test_cache_expires_entries_on_take_and_peek(fake_time):
    cache: BoundedTTLCache[int] = BoundedTTLCache(maxsize=4, ttl=10)
    cache.put("a", 1)
    cache.put("b", 2)

    fake_time.now += 11

    assert cache.take("a") is None
    assert cache.peek_owner("b") is None
    # 过期项在 peek 时即被移除
    assert len(cache) == 0


def test_cache_evicts_oldest_when_over_capacity(fake_time):
    cache: BoundedTTLCache[int] = BoundedTTLCache(maxsize=2, ttl=1000)
    cache.put("k1", 1)
    fake_time.now += 1
    cache.put("k2", 2)
    fake_time.now += 1
    cache.put("k3", 3)

    assert len(cache) == 2
    assert cache.take("k1") is None  # 最旧被淘汰
    assert cache.take("k3") == (3, 0.0)


def test_cache_put_evicts_expired_first(fake_time):
    cache: BoundedTTLCache[int] = BoundedTTLCache(maxsize=4, ttl=5)
    cache.put("old", 1)
    fake_time.now += 6
    cache.put("new", 2)

    assert cache.take("old") is None
    assert cache.take("new") == (2, 0.0)


def test_cache_discard_clear_and_constructor_validation():
    cache: BoundedTTLCache[int] = BoundedTTLCache(maxsize=2, ttl=10)
    cache.put("x", 1)
    cache.discard("x")
    assert cache.take("x") is None

    cache.put("y", 1)
    cache.clear()
    assert len(cache) == 0

    with pytest.raises(ValueError):
        BoundedTTLCache(maxsize=0, ttl=10)
    with pytest.raises(ValueError):
        BoundedTTLCache(maxsize=1, ttl=0)


# ---------- mail_service ----------
def test_safe_header_strips_crlf_and_render_falls_back():
    assert mail_service._safe_header("标题\r\nBcc: evil@example.com") == "标题Bcc: evil@example.com"
    assert mail_service._render("code={code}", code="1234") == "code=1234"
    # 占位符写错 → 返回空串，由调用方回退到默认文案
    assert mail_service._render("hi {missing}", code="1") == ""


def test_send_raises_when_host_missing():
    """SMTP 未配置时必须显式报错，而不是静默返回（否则注册验证码永远发不出去）。"""
    with pytest.raises(mail_service.MailError, match="SMTP 未配置"):
        mail_service._send("to@example.com", "主题", "正文", {})


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"smtp_host": "h", "smtp_port": "abc", "smtp_sender": "s@e.com"}, "端口不是数字"),
        ({"smtp_host": "h"}, "未配置发件人"),
        (
            {"smtp_host": "h", "smtp_sender": "s@e.com", "smtp_password": "enc:broken"},
            "密文无法解密",
        ),
        (
            {
                "smtp_host": "h",
                "smtp_sender": "s@e.com",
                "smtp_username": "u@e.com",
                "smtp_password": "plain-secret",
                "smtp_use_tls": "false",
            },
            "拒绝在明文通道",
        ),
    ],
)
def test_send_rejects_invalid_configuration(settings, expected):
    with pytest.raises(mail_service.MailError) as exc:
        mail_service._send("to@example.com", "主题", "正文", settings)
    assert expected in str(exc.value)


class _FakeSMTP:
    instances: list[_FakeSMTP] = []

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.calls: list[tuple] = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def starttls(self, context=None):
        self.calls.append(("starttls", context is not None))

    def sendmail(self, sender, to, message):
        self.calls.append(("sendmail", sender, to, message))


def _tls_settings(**overrides) -> dict[str, str]:
    settings = {
        "smtp_host": "smtp.example.com",
        "smtp_port": "465",
        "smtp_username": "u@example.com",
        "smtp_password": "secret",
        "smtp_sender": "sender@example.com",
        "smtp_use_tls": "true",
        "site_name": "培训平台",
    }
    settings.update(overrides)
    return settings


def test_send_uses_ssl_login_for_port_465(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(mail_service.smtplib, "SMTP_SSL", _FakeSMTP)

    mail_service._send("to@example.com", "主题", "正文", _tls_settings())

    instance = _FakeSMTP.instances[-1]
    assert instance.args[:2] == ("smtp.example.com", 465)
    assert instance.kwargs.get("context") is not None  # 显式校验证书
    assert ("login", "u@example.com", "secret") in instance.calls
    assert any(call[0] == "sendmail" for call in instance.calls)


def test_send_uses_starttls_for_other_tls_ports(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(mail_service.smtplib, "SMTP", _FakeSMTP)

    mail_service._send("to@example.com", "主题", "正文", _tls_settings(smtp_port="587"))

    instance = _FakeSMTP.instances[-1]
    assert ("starttls", True) in instance.calls
    assert ("login", "u@example.com", "secret") in instance.calls


def test_send_allows_anonymous_relay_without_credentials(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(mail_service.smtplib, "SMTP", _FakeSMTP)

    mail_service._send(
        "to@example.com",
        "主题",
        "正文",
        {
            "smtp_host": "relay.internal",
            "smtp_port": "25",
            "smtp_sender": "noreply@example.com",
            "smtp_use_tls": "false",
        },
    )

    instance = _FakeSMTP.instances[-1]
    assert not any(call[0] == "login" for call in instance.calls)
    assert any(call[0] == "sendmail" for call in instance.calls)


def test_send_wraps_smtp_errors_as_mail_error(monkeypatch):
    class _BoomSMTP(_FakeSMTP):
        def sendmail(self, sender, to, message):
            raise smtplib.SMTPException("535 auth failed")

    monkeypatch.setattr(mail_service.smtplib, "SMTP_SSL", _BoomSMTP)

    with pytest.raises(mail_service.MailError) as exc:
        mail_service._send("to@example.com", "主题", "正文", _tls_settings())
    assert "535 auth failed" in str(exc.value)


def test_send_safely_swallows_and_logs(caplog):
    def _boom():
        raise RuntimeError("smtp down")

    with caplog.at_level(logging.ERROR, logger="quizhub"):
        mail_service.send_safely(_boom)

    assert "邮件发送失败" in caplog.text
    assert "RuntimeError" in caplog.text


def test_message_builders_render_templates_and_fall_back(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(mail_service, "_send", lambda to, subject, body, settings: sent.append((to, subject, body)))

    mail_service.send_register_code({"mail_tpl_register": "验证码 {code}"}, "a@example.com", "123456")
    assert sent[-1][2] == "验证码 123456"

    # 占位符写错 → 回退默认文案，且不把带 {xxx} 的原文发出去
    mail_service.send_register_code({"mail_tpl_register": "{nope}"}, "a@example.com", "123456")
    assert "123456" in sent[-1][2]
    assert "{nope}" not in sent[-1][2]

    mail_service.send_register_code({}, "a@example.com", "999999")
    assert "999999" in sent[-1][2]

    mail_service.send_exam_publish(
        {"mail_tpl_exam_publish": "{exam_name} 截止 {end_at}"}, "a@example.com", "期末考试", "2026-12-31"
    )
    assert sent[-1][2] == "期末考试 截止 2026-12-31"

    mail_service.send_review_done(
        {"mail_tpl_review_done": "{exam_name} 成绩 {score}"}, "a@example.com", "期末考试", 88.5
    )
    assert sent[-1][2] == "期末考试 成绩 88.5"
