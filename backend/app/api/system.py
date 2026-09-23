"""系统管理路由：设置、SMTP 测试、注册审批、站点 Logo。"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import FILES_DIR
from app.core.deps import require_super
from app.core.security import MASKED_SECRET
from app.database import get_db
from app.models.user import User
from app.schemas.system import SettingsUpdateIn, SmtpTestIn
from app.services import mail_service, system_service
from app.services.audit_service import log as audit_log
from app.services.system_service import DEFAULT_SETTINGS, get_settings
from app.services.system_service import update_settings as update_settings_svc

router = APIRouter(prefix="/system", tags=["system"])


# ---------- 公开站点信息（无需登录，供前端主题色/站点名/Logo）----------
@router.get("/site")
def site_info(db: Session = Depends(get_db)):
    settings = get_settings(db, "general")
    return {
        "site_name": settings.get("site_name", "培训考试平台"),
        "brand_color": settings.get("brand_color", "#E60012"),
        "site_logo": settings.get("site_logo", ""),
    }


# ---------- Logo 上传（超管）----------
# 允许的图片扩展名与大小上限（2MB，足够 Logo 用途）。
# 刻意不支持 .svg：SVG 可内嵌 <script>，而 /files 与 logo 回源都是**同源公开读取**，
# 浏览器直接导航该 URL 即执行脚本 —— 构成存储型 XSS。需要矢量图请先转 PNG/WebP。
_LOGO_ALLOWED = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_LOGO_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_LOGO_MAX_BYTES = 2 * 1024 * 1024


@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_super),
):
    """上传站点 Logo：存到 data/files/，返回可公开访问的 URL 路径。"""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _LOGO_ALLOWED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"仅支持 {', '.join(sorted(_LOGO_ALLOWED))} 格式")
    # 先按声明大小快速拒绝，再分块读取并累计上限：
    # 原实现直接 await file.read() 再比较长度，超大 body 已先进入内存，构成内存耗尽面。
    limit_kb = _LOGO_MAX_BYTES // 1024
    if file.size and file.size > _LOGO_MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Logo 不能超过 {limit_kb}KB")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(256 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _LOGO_MAX_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Logo 不能超过 {limit_kb}KB")
        chunks.append(chunk)
    content = b"".join(chunks)
    # 固定文件名 logo<ext>，覆盖旧 Logo，避免残留堆积
    filename = f"logo{ext}"
    save_path = FILES_DIR / filename
    save_path.write_bytes(content)
    # 同一 Logo 只保留当前扩展名：清掉历史遗留的其它 logo.*（含不受支持的 logo.svg）
    for stale in FILES_DIR.glob("logo.*"):
        if stale != save_path:
            stale.unlink(missing_ok=True)
    # 访问路径（main.py 挂载 /files 到 data/files 目录）
    url = f"/files/{filename}"
    # 更新设置项 site_logo（存相对路径，域名无关，前后端同源可直接用）
    update_settings_svc(db, "general", {"site_logo": url})
    audit_log(db, user.id, "settings.logo_upload", "setting", "site_logo", {"url": url})
    return {"url": url}


@router.get("/logo/{name}")
def get_logo(name: str):
    """公开读取 Logo 文件（无需登录，供登录/注册页与布局直接展示）。"""
    # 仅允许 logo 前缀 + 受支持的图片扩展名，杜绝路径遍历与可执行静态内容回源
    base = os.path.basename(name)
    ext = os.path.splitext(base)[1].lower()
    if not base.startswith("logo") or ext not in _LOGO_ALLOWED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    target = (FILES_DIR / base).resolve()
    try:
        target.relative_to(FILES_DIR.resolve())
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found") from None
    if not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    # 显式声明媒体类型并禁止嗅探：即使目录里混入可执行内容也按图片处理
    return FileResponse(
        target,
        media_type=_LOGO_CONTENT_TYPES[ext],
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{base}"',
        },
    )


# 设置分组的元信息（前端按分类渲染表单）
CATEGORIES = [
    {"key": "general", "label": "基础设置"},
    {"key": "exam", "label": "考试默认值"},
    {"key": "upload", "label": "上传限制"},
    {"key": "smtp", "label": "SMTP 邮件"},
    {"key": "register", "label": "注册与审批"},
    {"key": "mail_tpl", "label": "邮件模板"},
]


@router.get("/categories")
def categories(_user: User = Depends(require_super)):
    """返回设置分类与每类下的字段定义。"""
    out = []
    for cat in CATEGORIES:
        fields = [
            {"key": k, "label": _label(k), "encrypted": enc, "value_type": _value_type(k)}
            for k, (_, c, enc) in DEFAULT_SETTINGS.items()
            if c == cat["key"]
        ]
        out.append({**cat, "fields": fields})
    return out


_LABELS = {
    "site_name": "站点名称",
    "site_logo": "站点Logo URL",
    "brand_color": "主题色",
    "default_pass_score": "默认及格线",
    "default_exam_duration_min": "默认考试时长(分钟)",
    "max_questions_per_exam": "单场最大题数",
    "mock_keep_definitions": "模拟考试保留的未提交试卷数",
    "upload_max_size_mb": "上传大小上限(MB)",
    "upload_allowed_ext": "允许上传扩展名",
    "smtp_host": "SMTP 服务器",
    "smtp_port": "SMTP 端口",
    "smtp_username": "SMTP 用户名",
    "smtp_password": "SMTP 密码",
    "smtp_sender": "发件人地址",
    "smtp_use_tls": "启用SSL/TLS",
    "register_open": "开放注册",
    "new_user_need_approve": "新用户需审批",
    "register_group_required": "注册时必选分组",
    "register_allowed_group_ids": "允许公开注册加入的分组",
    "register_allowed_email_suffixes": "允许注册邮箱后缀(逗号分隔,留空不限制)",
    "mail_tpl_register": "注册验证码模板",
    "mail_tpl_exam_publish": "考试发布通知模板",
    "mail_tpl_review_done": "成绩公布通知模板",
}


def _label(key: str) -> str:
    return _LABELS.get(key, key)


def _value_type(key: str) -> str:
    if key in ("smtp_use_tls", "register_open", "new_user_need_approve", "register_group_required"):
        return "bool"
    if key in (
        "default_pass_score",
        "default_exam_duration_min",
        "max_questions_per_exam",
        "mock_keep_definitions",
        "upload_max_size_mb",
        "smtp_port",
    ):
        return "number"
    return "text"


@router.get("/settings")
def list_settings(category: str | None = None, db: Session = Depends(get_db), _user: User = Depends(require_super)):
    settings = get_settings(db, category)
    # 补充 encrypted 标记
    out = []
    for k, v in settings.items():
        enc = DEFAULT_SETTINGS.get(k, ("", "", False))[2]
        if category:
            # 敏感（加密）字段在列表 API 仅返回占位符，绝不回传明文，避免凭据泄露
            value = MASKED_SECRET if enc else v
            out.append({"key": k, "value": value, "encrypted": enc})
        else:
            _, cat, e = DEFAULT_SETTINGS.get(k, ("", "general", False))
            value = MASKED_SECRET if e else v
            out.append({"key": k, "value": value, "category": cat, "encrypted": e})
    return out


@router.put("/settings")
def update_settings(payload: SettingsUpdateIn, db: Session = Depends(get_db), user: User = Depends(require_super)):
    try:
        system_service.update_settings(db, payload.category, payload.updates)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except RuntimeError as exc:
        # 未配置 TRAINING_ENC_KEY 时保存加密设置会抛 RuntimeError：给可操作提示而不是 500
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    audit_log(db, user.id, "settings.update", "setting", payload.category, {"keys": list(payload.updates.keys())})
    return {"success": True}


@router.post("/smtp/test")
def smtp_test(payload: SmtpTestIn, db: Session = Depends(get_db), user: User = Depends(require_super)):
    # 防滥用测试邮件发件：单管理员 5 次/小时
    from app.core.rate_limit import check

    check(f"smtp-test:user:{user.id}", 5, 3600, "SMTP 测试")
    # 合并 general 设置，让 From 显示名称用上站点名
    settings = {**get_settings(db, "general"), **get_settings(db, "smtp")}
    if not settings.get("smtp_host"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先配置 SMTP 服务器")
    try:
        # 同步发送：后台发送会把认证/连接错误吞掉，管理员只看到"已发送"却收不到邮件，
        # 因此这里必须等真正的投递结果，并把 SMTP 的原始错误回显给管理员。
        mail_service.send_smtp_test(settings, payload.to_email)
    except mail_service.MailError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"测试邮件发送失败：{exc}") from exc
    return {"success": True, "message": f"测试邮件已发送至 {payload.to_email}"}
