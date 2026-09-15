"""系统设置读写。

提供分组按 category 读取/写入；SMTP 密码等敏感项加密。
"""

from __future__ import annotations

import logging
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import MASKED_SECRET, decrypt_value, encrypt_value
from app.models.system import Setting

logger = logging.getLogger("quizhub")

DEFAULT_SETTINGS: dict[str, tuple[str, str, bool]] = {
    # key: (value, category, encrypted)
    "site_name": ("培训考试平台", "general", False),
    "site_logo": ("", "general", False),
    "brand_color": ("#E60012", "general", False),
    # 排行榜是否对用户端可见（关闭后用户端不显示排行入口，且路由拦截直接访问）
    "rank_visible": ("true", "general", False),
    "default_pass_score": ("60", "exam", False),
    "default_exam_duration_min": ("90", "exam", False),
    "max_questions_per_exam": ("100", "exam", False),
    "upload_max_size_mb": ("10", "upload", False),
    "upload_allowed_ext": (".xlsx", "upload", False),
    "smtp_host": ("", "smtp", False),
    "smtp_port": ("465", "smtp", False),
    "smtp_username": ("", "smtp", False),
    "smtp_password": ("", "smtp", True),
    "smtp_sender": ("", "smtp", False),
    "smtp_use_tls": ("true", "smtp", False),
    "register_open": ("true", "register", False),
    "new_user_need_approve": ("false", "register", False),
    # 注册时是否必须选择分组（true 则至少选一个）
    "register_group_required": ("false", "register", False),
    "register_allowed_group_ids": ("", "register", False),
    # 允许注册的邮箱后缀，逗号分隔，如 "@company.com,@edu.cn"；留空表示不限制
    "register_allowed_email_suffixes": ("", "register", False),
    "mail_tpl_register": (
        "您的注册验证码是：{code}，有效期 10 分钟。",
        "mail_tpl",
        False,
    ),
    "mail_tpl_exam_publish": (
        "新考试「{exam_name}」已发布，请在 {end_at} 前完成。",
        "mail_tpl",
        False,
    ),
    "mail_tpl_review_done": (
        "您的考试「{exam_name}」成绩已公布：{score} 分。",
        "mail_tpl",
        False,
    ),
}


def ensure_defaults(db: Session) -> None:
    """初始化缺失的默认设置项。"""
    existing = {row[0] for row in db.execute(select(Setting.setting_key)).all()}
    for key, (val, cat, enc) in DEFAULT_SETTINGS.items():
        if key in existing:
            continue
        db.add(
            Setting(
                setting_key=key,
                value=encrypt_value(val) if enc else val,
                category=cat,
                encrypted=enc,
            )
        )
    db.commit()


def get_settings(db: Session, category: str | None = None) -> dict[str, str]:
    """读取设置项。

    历史遗留的 "plain:" 前缀行（旧版未配置 ENC_KEY 时的回退写法，见
    docs/audit_report.md SEC-P1-6）已无法解密。此处对其降级为空字符串并继续，
    而不是让整个分类抛 RuntimeError —— 否则单个坏行会让
    GET /system/settings 整体 500，管理端连邮件设置页都打不开。
    真正的修复由 migrate_2026_08_28.migrate_legacy_plain_settings 完成。
    """
    stmt = select(Setting)
    if category:
        stmt = stmt.where(Setting.category == category)
    out: dict[str, str] = {}
    for row in db.execute(stmt).scalars():
        if not row.encrypted:
            out[row.setting_key] = row.value
            continue
        try:
            out[row.setting_key] = decrypt_value(row.value)
        except RuntimeError:
            # 遗留不可解密值：降级为空，保证设置页仍可打开并由管理员重填
            logger.warning("[settings] 设置项 %s 解密失败，已降级为空值（需重新填写）", row.setting_key)
            out[row.setting_key] = ""
    return out


def update_settings(db: Session, category: str, updates: dict[str, Any]) -> None:
    if category not in {value[1] for value in DEFAULT_SETTINGS.values()}:
        raise ValueError("设置分类无效")
    for key, val in updates.items():
        if key not in DEFAULT_SETTINGS:
            raise ValueError(f"设置项无效: {key}")
        _, cat, enc = DEFAULT_SETTINGS[key]
        if cat != category:
            raise ValueError(f"设置项不属于分类 {category}: {key}")
        if enc and str(val) == MASKED_SECRET:
            # 设置列表接口返回的掩码只用于展示，不能覆盖数据库中的真实密文。
            continue
        row = db.execute(select(Setting).where(Setting.setting_key == key)).scalar_one_or_none()
        sval = str(val)
        _validate_value(key, sval)
        if enc:
            sval = encrypt_value(sval)
        if row is None:
            db.add(Setting(setting_key=key, value=sval, category=cat, encrypted=enc))
        else:
            row.value = sval
    db.commit()


def _validate_value(key: str, value: str) -> None:
    if key in {"rank_visible", "smtp_use_tls", "register_open", "new_user_need_approve", "register_group_required"}:
        if value.lower() not in {"true", "false"}:
            raise ValueError(f"{key} 必须是 true 或 false")
    elif key == "smtp_port":
        if not value.isdigit() or not 1 <= int(value) <= 65535:
            raise ValueError("SMTP 端口必须在 1~65535 之间")
    elif key in {"default_pass_score", "default_exam_duration_min", "max_questions_per_exam", "upload_max_size_mb"}:
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"{key} 必须是数字") from None
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{key} 必须是非负有限数字")
    elif key == "register_allowed_group_ids":
        for group_id in value.replace("，", ",").split(","):
            if group_id.strip() and (not group_id.strip().isdigit() or int(group_id) <= 0):
                raise ValueError("公开注册分组 ID 必须是正整数")
    elif key == "upload_allowed_ext":
        extensions = [ext.strip().lower() for ext in value.replace("，", ",").split(",") if ext.strip()]
        if not extensions or any(not ext.startswith(".") or ext != ".xlsx" for ext in extensions):
            raise ValueError("当前仅支持 .xlsx 上传")
    elif key == "smtp_host":
        # 主机名会进入 socket.getaddrinfo：拒绝空白与控制字符，
        # 避免配置项被用于注入或指向异常目标。
        host = value.strip()
        if not host or any(c in value for c in "\r\n\t ") or "/" in value or ":" in value:
            raise ValueError("SMTP 服务器必须是合法主机名（不含协议、端口、空格）")
    elif key in {"smtp_sender", "smtp_username", "site_name", "smtp_password"} and any(c in value for c in "\r\n"):
        # 这些值会进入邮件头或 SMTP 认证；CR/LF 会导致邮件头注入（伪造 Bcc/From）
        raise ValueError(f"{key} 不能包含换行符")
