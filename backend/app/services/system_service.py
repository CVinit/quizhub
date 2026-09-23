"""系统设置读写。

提供分组按 category 读取/写入；SMTP 密码等敏感项加密。
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import MASKED_SECRET, decrypt_value, encrypt_value
from app.models.system import Setting

logger = logging.getLogger("quizhub")

# 模拟考试保留的未提交定义数上限。与 exam/mock.MOCK_KEEP_MAX 同值：本模块处于更底层
# （exam/mock 反向依赖它），不能 import exam 包，故此处保留字面量并在此注明。
_MOCK_KEEP_MAX = 100

# 布尔型设置项：写入时统一归一为小写 "true"/"false"。读取端（site_info、
# 各布尔开关）按小写字面量比较，若不归一，"TRUE" 会被接受入库却读成 false。
BOOL_SETTING_KEYS = frozenset({"smtp_use_tls", "register_open", "new_user_need_approve", "register_group_required"})

# 注册邮箱后缀的合法形态：@ + 域名（至少一个点；标签不以连字符开头/结尾）。
# 强制 "@" 前缀是安全控制的一部分，见 _validate_value 的 register_allowed_email_suffixes 分支。
_EMAIL_SUFFIX_RE = re.compile(r"^@[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

DEFAULT_SETTINGS: dict[str, tuple[str, str, bool]] = {
    # key: (value, category, encrypted)
    "site_name": ("培训考试平台", "general", False),
    "site_logo": ("", "general", False),
    "brand_color": ("#E60012", "general", False),
    "default_pass_score": ("60", "exam", False),
    "default_exam_duration_min": ("90", "exam", False),
    "max_questions_per_exam": ("100", "exam", False),
    # 每个用户保留的「未提交」模拟考试定义数：超出后清理最早的未提交考试，
    # 已交卷的模拟成绩不受影响（见 exam/mock._cleanup_stale_mock_defs）。
    # 上限 100 与 exam/mock.MOCK_KEEP_MAX 保持一致（本模块是低层模块，不反向 import exam 包）。
    "mock_keep_definitions": ("5", "exam", False),
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

    读取时同时看 encrypted 标记与值前缀（见 _decrypt_row_value）：
    曾出现「值是 enc: 密文但 encrypted=0」的脏数据（旧迁移把行置为 0 后，
    管理员经设置页回填密码，而旧版 update_settings 不回写 encrypted 标记），
    若只信标记位，密文会被当成明文密码去登录 SMTP，导致邮件永久发不出去。
    """
    stmt = select(Setting)
    if category:
        stmt = stmt.where(Setting.category == category)
    out: dict[str, str] = {}
    for row in db.execute(stmt).scalars():
        out[row.setting_key] = _decrypt_row_value(row)
    return out


def _decrypt_row_value(row: Setting) -> str:
    """按行读取设置值，兼容 encrypted 标记与实际存储不一致的历史脏数据。

    解密失败必须降级为空串而不是向上抛：`decrypt_value` 对「未配置密钥/plain: 遗留值」
    抛 RuntimeError，密钥轮换或密文损坏时 Fernet 抛 `InvalidToken`，而
    `TRAINING_ENC_KEY` **非空但格式非法**（误填、被截断）时 `Fernet(...)` 抛的是
    `ValueError` —— 三者互不继承，必须一并捕获。否则单个坏行/坏密钥会让
    GET /system/settings 直接 500，管理员连重填密码的页面都打不开，
    注册/成绩通知邮件链路也会一并 500。
    """
    if not row.encrypted and not row.value.startswith(("enc:", "plain:")):
        return row.value
    try:
        return decrypt_value(row.value)
    except (RuntimeError, ValueError, InvalidToken):
        # 遗留/损坏的不可解密值：降级为空，保证设置页仍可打开并由管理员重填
        logger.warning("[settings] 设置项 %s 解密失败，已降级为空值（需重新填写）", row.setting_key)
        return ""


def update_settings(db: Session, category: str, updates: dict[str, Any]) -> None:
    if category not in {value[1] for value in DEFAULT_SETTINGS.values()}:
        raise ValueError("设置分类无效")
    # 一次取回本分类的全部既有行，避免逐 key 点查（一次保存最多 22 次 SELECT）。
    existing_rows: dict[str, Setting] = {
        setting_row.setting_key: setting_row
        for setting_row in db.execute(select(Setting).where(Setting.category == category)).scalars()
    }
    # 历史脏数据可能 category 与 DEFAULT_SETTINGS 不一致，按 key 再兜底取一次。
    missing_keys = [k for k in updates if k in DEFAULT_SETTINGS and k not in existing_rows]
    if missing_keys:
        for setting_row in db.execute(select(Setting).where(Setting.setting_key.in_(missing_keys))).scalars():
            existing_rows[setting_row.setting_key] = setting_row
    for key, val in updates.items():
        if key not in DEFAULT_SETTINGS:
            raise ValueError(f"设置项无效: {key}")
        _, cat, enc = DEFAULT_SETTINGS[key]
        if cat != category:
            raise ValueError(f"设置项不属于分类 {category}: {key}")
        if enc and str(val) == MASKED_SECRET:
            # 设置列表接口返回的掩码只用于展示，不能覆盖数据库中的真实密文。
            continue
        row = existing_rows.get(key)
        sval = str(val)
        _validate_value(key, sval)
        if key in BOOL_SETTING_KEYS:
            sval = sval.lower()  # 归一化，保证读取端的 "true" 比较成立
        if enc:
            sval = encrypt_value(sval)
        if row is None:
            db.add(Setting(setting_key=key, value=sval, category=cat, encrypted=enc))
        else:
            row.value = sval
            # encrypted 标记必须随写入一起回写：历史行的标记可能为 0（旧迁移清空
            # plain: 行时置 0），若不同步，密文会被 get_settings 当作明文返回，
            # SMTP 密码、加密设置将整体失效。
            row.encrypted = enc
            row.category = cat
    db.commit()


def _validate_value(key: str, value: str) -> None:
    if key in BOOL_SETTING_KEYS:
        if value.lower() not in {"true", "false"}:
            raise ValueError(f"{key} 必须是 true 或 false")
    elif key == "smtp_port":
        if not value.isdigit() or not 1 <= int(value) <= 65535:
            raise ValueError("SMTP 端口必须在 1~65535 之间")
    elif key == "default_pass_score":
        # 及格线是百分制（与 grading.is_passed 的判定口径一致），必须落在 0~100
        try:
            number = float(value)
        except ValueError:
            raise ValueError("default_pass_score 必须是数字") from None
        if not math.isfinite(number) or not 0 <= number <= 100:
            raise ValueError("default_pass_score 必须在 0~100 之间")
    elif key in {"default_exam_duration_min", "max_questions_per_exam", "mock_keep_definitions"}:
        # 消费方按 int() 解析（exam/mock.py 用 int(settings.get("default_exam_duration_min"))）。
        # 原先用 float() 校验会放行 "90.5"：保存返回 200，但用户开考时 int("90.5") 抛
        # ValueError → 500。校验口径必须与消费口径一致，否则错误在另一条链路才爆发。
        if not value.isdigit() or len(value) > 9 or int(value) < 1:
            raise ValueError(f"{key} 必须是正整数")
        if key == "mock_keep_definitions" and int(value) > _MOCK_KEEP_MAX:
            raise ValueError(f"mock_keep_definitions 不能超过 {_MOCK_KEEP_MAX}")
    elif key == "upload_max_size_mb":
        # 消费方按 float() 解析（api/questions.py、api/users.py），允许小数 MB
        try:
            number = float(value)
        except ValueError:
            raise ValueError("upload_max_size_mb 必须是数字") from None
        if not math.isfinite(number) or number < 0:
            raise ValueError("upload_max_size_mb 必须是非负有限数字")
    elif key == "register_allowed_group_ids":
        for group_id in value.replace("，", ",").split(","):
            if group_id.strip() and (not group_id.strip().isdigit() or int(group_id) <= 0):
                raise ValueError("公开注册分组 ID 必须是正整数")
    elif key == "register_allowed_email_suffixes":
        # 该值是比较用的安全控制（check_email_suffix 用 endswith 判定），必须强制以 "@" 开头：
        # 否则管理员填 "company.com" 时 "evil-company.com" 也会命中，白名单静默 fail-open。
        # 留空表示不限制，允许。
        for suffix in value.replace("，", ",").split(","):
            item = suffix.strip().lower()
            if not item:
                continue
            if not _EMAIL_SUFFIX_RE.match(item):
                raise ValueError("注册邮箱后缀必须以 @ 开头且为合法域名，如 @company.com（多个用逗号分隔，留空不限制）")
    elif key == "upload_allowed_ext":
        extensions = [ext.strip().lower() for ext in value.replace("，", ",").split(",") if ext.strip()]
        if not extensions or any(ext != ".xlsx" for ext in extensions):
            raise ValueError("当前仅支持 .xlsx 上传")
    elif key == "smtp_host":
        # 主机名会进入 socket.getaddrinfo/smtplib.connect：拒绝空白与控制字符，
        # 避免配置项被用于注入；并拒绝回环/链路本地地址——否则超管可借
        # 「SMTP 测试」接口把连接错误当 SSRF 探测 oracle（云元数据/本机服务）。
        host = value.strip()
        if not host or any(c in value for c in "\r\n\t ") or "/" in value or ":" in value:
            raise ValueError("SMTP 服务器必须是合法主机名（不含协议、端口、空格）")
        _reject_internal_host(host)
    elif key in {"smtp_sender", "smtp_username", "site_name", "smtp_password"} and any(c in value for c in "\r\n"):
        # 这些值会进入邮件头或 SMTP 认证；CR/LF 会导致邮件头注入（伪造 Bcc/From）
        raise ValueError(f"{key} 不能包含换行符")


def _reject_internal_host(host: str) -> None:
    """拒绝指向回环/链路本地/组播/未指定地址的 SMTP 主机（管理员侧 SSRF 加固）。

    只拦截真正的 SSRF 靶点：云元数据（169.254.169.254）、本机回环服务。
    **不**拦截私网地址——企业内部 SMTP 中继通常就在 10/172/192 段，一刀切会破坏
    合法部署；而调用者本就是掌握服务器配置的超管，风险有限。

    尽力而为：主机名解析失败（离线/DNS 暂不可用）时不阻断配置，真正的连接失败会由
    `mail_service` 以 `MailError` 明确回显。

    Args:
        host: 已通过字符校验的 SMTP 主机名或字面 IP。

    Raises:
        ValueError: 任一解析结果属于回环/链路本地/组播/未指定地址。
    """
    import ipaddress
    import socket

    addresses: list[str] = []
    try:
        addresses.append(str(ipaddress.ip_address(host)))
    except ValueError:
        try:
            addresses = [str(info[4][0]) for info in socket.getaddrinfo(host, None)]
        except OSError:
            logger.warning("[settings] SMTP 主机 %s 无法解析，跳过地址校验", host)
            return
    for raw in addresses:
        try:
            # IPv6 链路本地地址可能带 %scope 后缀，去掉后再判定
            ip = ipaddress.ip_address(str(raw).split("%", 1)[0])
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            raise ValueError("SMTP 服务器不能指向回环/链路本地/组播地址")
