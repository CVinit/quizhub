"""邮件服务：基于系统设置中的 SMTP 配置发送。

开发期若 SMTP 未配置，回退为打印日志，保证注册流程可走通。
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.utils import formataddr

logger = logging.getLogger("quizhub")

_CRLF = ("\r", "\n")


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


def send_review_done(settings: dict[str, str], to_email: str, exam_name: str, score: object) -> None:
    tpl = settings.get("mail_tpl_review_done", "您的考试「{exam_name}」成绩已公布：{score} 分。")
    body = _render(tpl, exam_name=exam_name, score=score) or f"您的考试「{exam_name}」成绩已公布：{score} 分。"
    _send(to_email, "成绩公布", body, settings)


def _send(to_email: str, subject: str, body: str, settings: dict[str, str]) -> None:
    host = settings.get("smtp_host", "")
    if not host:
        logger.warning("[mail] SMTP 未配置，邮件未发送")
        return
    port = int(settings.get("smtp_port", "465"))
    username = settings.get("smtp_username", "")
    password = settings.get("smtp_password", "")
    sender = settings.get("smtp_sender", username)
    use_tls = settings.get("smtp_use_tls", "true").lower() == "true"

    # 拒绝在无加密通道上发送凭据：明文 SMTP 会把邮箱口令暴露给链路上任何一跳
    if not use_tls and username and password:
        logger.error("[mail] smtp_use_tls=false 时拒绝发送账号密码，邮件未发送")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = _safe_header(subject)
    msg["From"] = formataddr((_safe_header(settings.get("site_name", "培训考试平台")), _safe_header(sender)))
    msg["To"] = _safe_header(to_email)

    # 显式使用校验证书的 SSL 上下文，避免落入未校验或不可配置的默认行为
    context = ssl.create_default_context()

    if use_tls and port in (465,):
        with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as s:
            if username and password:
                s.login(username, password)
            s.sendmail(sender, [to_email], msg.as_string())
    elif use_tls:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls(context=context)
            if username and password:
                s.login(username, password)
            s.sendmail(sender, [to_email], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=15) as s:
            # 无账号密码的匿名中继（如内网测试 SMTP）仍允许，避免破坏既有部署
            s.sendmail(sender, [to_email], msg.as_string())
