"""邮件服务：基于系统设置中的 SMTP 配置发送。

开发期若 SMTP 未配置，回退为打印日志，保证注册流程可走通。
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

logger = logging.getLogger("quizhub")


def _render(tpl: str, **kwargs: object) -> str:
    try:
        return tpl.format(**kwargs)
    except (KeyError, IndexError):
        return tpl


def send_register_code(settings: dict[str, str], to_email: str, code: str) -> None:
    tpl = settings.get("mail_tpl_register", "您的注册验证码是：{code}")
    _send(to_email, "注册验证码", _render(tpl, code=code), settings)


def send_exam_publish(settings: dict[str, str], to_email: str, exam_name: str, end_at: str) -> None:
    tpl = settings.get("mail_tpl_exam_publish", "新考试「{exam_name}」已发布，请在 {end_at} 前完成。")
    _send(to_email, "考试通知", _render(tpl, exam_name=exam_name, end_at=end_at), settings)


def send_review_done(settings: dict[str, str], to_email: str, exam_name: str, score: object) -> None:
    tpl = settings.get("mail_tpl_review_done", "您的考试「{exam_name}」成绩已公布：{score} 分。")
    _send(to_email, "成绩公布", _render(tpl, exam_name=exam_name, score=score), settings)


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

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.get("site_name", "培训考试平台"), sender))
    msg["To"] = to_email

    if use_tls and port in (465,):
        with smtplib.SMTP_SSL(host, port, timeout=15) as s:
            if username and password:
                s.login(username, password)
            s.sendmail(sender, [to_email], msg.as_string())
    elif use_tls:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            if username and password:
                s.login(username, password)
            s.sendmail(sender, [to_email], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=15) as s:
            if username and password:
                s.login(username, password)
            s.sendmail(sender, [to_email], msg.as_string())
