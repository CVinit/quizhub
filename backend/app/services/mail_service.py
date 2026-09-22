"""邮件服务：基于系统设置中的 SMTP 配置发送。

SMTP 未配置时不再静默返回：`ensure_configured()` / `_send()` 统一抛 `MailError`，
由调用方决定是展示给管理员（SMTP 测试）还是让用户拿到明确的 503（注册验证码），
避免"界面提示已发送、实际没发出去"的静默失败。
投递失败（认证/连接/发件人被拒）同样统一抛 MailError。
"""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from collections.abc import Callable
from email.mime.text import MIMEText
from email.utils import formataddr

logger = logging.getLogger("quizhub")

_CRLF = ("\r", "\n")

# smtplib 的异常消息会内嵌收发件人地址（如
# `SMTPRecipientsRefused({'alice@example.com': (550, ...)})`）。地址属 PII，
# 既不能进日志，也不能经 MailError 回显到管理端界面，因此记录/抛出前统一脱敏。
_EMAIL_RE = re.compile(r"[^\s@<>()\[\]{},;:\"']+@[^\s@<>()\[\]{},;:\"']+")


def _redact(value: str) -> str:
    """把文本中形如邮箱的片段替换为 `<redacted>`。

    Args:
        value: 待脱敏文本（通常来自异常消息）。

    Returns:
        脱敏后的文本；邮箱地址被替换，其余诊断信息保留。
    """
    return _EMAIL_RE.sub("<redacted>", value)


class MailError(RuntimeError):
    """邮件发送失败：配置缺失、认证失败或连接异常。"""


def ensure_configured(settings: dict[str, str]) -> None:
    """校验 SMTP 已配置，未配置则抛 `MailError`（fail-closed）。

    供「必须发信才能继续」的同步路径（注册验证码）在写入验证码**之前**调用，
    让用户拿到明确错误，而不是"提示已发送、邮箱永远收不到"。

    Args:
        settings: 系统设置字典。

    Raises:
        MailError: 未配置 smtp_host。
    """
    if not (settings.get("smtp_host") or "").strip():
        raise MailError("SMTP 未配置，无法发送邮件；请在系统设置中配置邮件服务")


def send_safely(fn: Callable[..., None], *args: object) -> None:
    """后台任务包装：把发送异常显式记录到日志。

    Starlette 的后台任务异常不会影响已返回的响应，若不显式记录，
    管理员只会看到"验证码已发送"而完全不知道 SMTP 出了问题。
    """
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001  后台发送不能让异常逃逸
        # 只记录异常类型与脱敏后的消息：异常消息可能内嵌收件人邮箱（PII）
        logger.error("[mail] 邮件发送失败：%s: %s", type(exc).__name__, _redact(str(exc)))


def _safe_header(value: str) -> str:
    """剔除 CR/LF，防止邮件头注入（伪造 Bcc/From 等）。

    Args:
        value: 待写入邮件头的字符串。

    Returns:
        去除回车换行后的字符串。
    """
    return "".join(ch for ch in value if ch not in _CRLF)


def _render(tpl: str, **kwargs: object) -> str:
    try:
        return tpl.format(**kwargs)
    except (KeyError, IndexError):
        # 模板占位符写错时不应把「带 {xxx} 的原文」发给用户，
        # 记录告警以便管理员发现，同时退回不带占位符的通用文案。
        logger.warning("[mail] 邮件模板占位符不匹配，已改用默认文案: %r", tpl[:60])
        return ""


def send_register_code(settings: dict[str, str], to_email: str, code: str) -> None:
    tpl = settings.get("mail_tpl_register", "您的注册验证码是：{code}")
    body = _render(tpl, code=code) or f"您的注册验证码是：{code}，有效期 10 分钟。"
    _send(to_email, "注册验证码", body, settings)


def send_exam_publish(settings: dict[str, str], to_email: str, exam_name: str, end_at: str) -> None:
    tpl = settings.get("mail_tpl_exam_publish", "新考试「{exam_name}」已发布，请在 {end_at} 前完成。")
    body = _render(tpl, exam_name=exam_name, end_at=end_at) or f"新考试「{exam_name}」已发布，请在 {end_at} 前完成。"
    _send(to_email, "考试通知", body, settings)


def send_exam_publish_many(settings: dict[str, str], recipients: list[str], exam_name: str, end_at: str) -> None:
    """批量发送考试通知（单个后台任务内串行发送）。

    发布考试时若为每位收件人各挂一个 BackgroundTask，任务对象与收件人列表会随
    活跃用户数线性增长；这里收敛为一个任务，并汇总失败数。
    仍按收件人各建一次 SMTP 连接（与单发错误语义一致），长期方案是接入邮件队列。

    Args:
        settings: 系统设置（含 SMTP 与模板）。
        recipients: 收件人邮箱列表。
        exam_name: 考试名称。
        end_at: 截止时间文案。
    """
    failed = 0
    for to_email in recipients:
        try:
            send_exam_publish(settings, to_email, exam_name, end_at)
        except MailError:
            failed += 1
    if failed:
        logger.warning("[mail] 考试通知发送失败 %d/%d", failed, len(recipients))


def send_review_done(settings: dict[str, str], to_email: str, exam_name: str, score: object) -> None:
    tpl = settings.get("mail_tpl_review_done", "您的考试「{exam_name}」成绩已公布：{score} 分。")
    body = _render(tpl, exam_name=exam_name, score=score) or f"您的考试「{exam_name}」成绩已公布：{score} 分。"
    _send(to_email, "成绩公布", body, settings)


def send_review_done_many(settings: dict[str, str], recipients: list[tuple[str, object]], exam_name: str) -> None:
    """批量发送「成绩已公布」通知（单个后台任务内串行发送）。

    与 `send_exam_publish_many` 同口径：公布成绩可能涉及大量考生，逐人挂一个
    BackgroundTask 会让任务对象随人数线性增长，这里收敛为一个任务并汇总失败数。

    Args:
        settings: 系统设置（含 SMTP 与模板）。
        recipients: (收件邮箱, 分数) 列表。
        exam_name: 考试名称。
    """
    failed = 0
    for to_email, score in recipients:
        try:
            send_review_done(settings, to_email, exam_name, score)
        except MailError:
            failed += 1
    if failed:
        logger.warning("[mail] 成绩公布通知发送失败 %d/%d", failed, len(recipients))


def send_smtp_test(settings: dict[str, str], to_email: str) -> None:
    """发送 SMTP 配置测试邮件（管理端「发送测试」入口）。

    `_send` 是内部实现，路由层不应直接调用私有符号；这里提供语义明确的公有入口，
    异常仍统一为 `MailError`。

    Args:
        settings: 合并后的系统设置（general + smtp）。
        to_email: 测试收件人。

    Raises:
        MailError: 配置缺失或投递失败。
    """
    site_name = settings.get("site_name", "培训考试平台")
    _send(to_email, f"{site_name} SMTP 测试", "这是一封测试邮件，SMTP 配置正常。", settings)


def _send(to_email: str, subject: str, body: str, settings: dict[str, str]) -> None:
    ensure_configured(settings)
    host = (settings.get("smtp_host") or "").strip()
    try:
        port = int(settings.get("smtp_port") or "465")
    except ValueError as exc:
        raise MailError(f"SMTP 端口不是数字：{settings.get('smtp_port')!r}") from exc
    username = (settings.get("smtp_username") or "").strip()
    password = settings.get("smtp_password") or ""
    sender = (settings.get("smtp_sender") or "").strip() or username
    use_tls = (settings.get("smtp_use_tls") or "true").lower() == "true"

    if not sender:
        raise MailError("未配置发件人地址（smtp_sender 或 smtp_username）")
    if password.startswith("enc:"):
        # 走到这里说明密文未被解密：历史脏数据或 ENC_KEY 不一致。
        # 继续发送只会得到 smtplib 的认证失败，提示管理员重填更直接。
        raise MailError("SMTP 密码密文无法解密，请在系统设置中重新保存 SMTP 密码")

    # 拒绝在无加密通道上发送凭据：明文 SMTP 会把邮箱口令暴露给链路上任何一跳
    if not use_tls and username and password:
        raise MailError("已关闭 SSL/TLS，拒绝在明文通道发送账号密码；请开启后重试")

    # 信封地址同样要清洗：msg["To"] 只保护了邮件头，而 smtplib 的 RCPT TO / MAIL FROM
    # 直接使用原始字符串。库中可能存在 Excel 导入的、含内部 \r\n 的邮箱（导入仅校验
    # 是否含 "@"），不清洗就构成 SMTP 命令注入；Python 3.12+ 的 putcmd 会抛 ValueError，
    # 必须一并捕获，否则会逃出下面的 except 变成未处理 500。
    recipient = _safe_header(to_email)
    envelope_sender = _safe_header(sender)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = _safe_header(subject)
    msg["From"] = formataddr((_safe_header(settings.get("site_name", "培训考试平台")), envelope_sender))
    msg["To"] = recipient

    # 显式使用校验证书的 SSL 上下文，避免落入未校验或不可配置的默认行为
    context = ssl.create_default_context()

    try:
        if use_tls and port in (465,):
            with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as s:
                if username and password:
                    s.login(username, password)
                s.sendmail(envelope_sender, [recipient], msg.as_string())
        elif use_tls:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls(context=context)
                if username and password:
                    s.login(username, password)
                s.sendmail(envelope_sender, [recipient], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                # 无账号密码的匿名中继（如内网测试 SMTP）仍允许，避免破坏既有部署
                s.sendmail(envelope_sender, [recipient], msg.as_string())
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        # 认证失败/连接失败/发件人被拒：包装成可读错误，供 SMTP 测试接口回显。
        # 必须脱敏：smtplib 异常消息携带收/发件人邮箱。
        raise MailError(f"{type(exc).__name__}: {_redact(str(exc))}") from exc
