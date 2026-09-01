"""系统管理路由：设置、SMTP 测试、注册审批、站点 Logo。"""

from __future__ import annotations

import os

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import FILES_DIR
from app.core.deps import require_super
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
        "rank_visible": settings.get("rank_visible", "true") == "true",
    }


# ---------- Logo 上传（超管）----------
# 允许的图片扩展名与大小上限（2MB，足够 Logo 用途）
_LOGO_ALLOWED = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
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
    content = await file.read()
    if len(content) > _LOGO_MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Logo 不能超过 {_LOGO_MAX_BYTES // 1024}KB")
    # 固定文件名 logo<ext>，覆盖旧 Logo，避免残留堆积
    filename = f"logo{ext}"
    save_path = FILES_DIR / filename
    save_path.write_bytes(content)
    # 访问路径（main.py 挂载 /files 到 data/files 目录）
    url = f"/files/{filename}"
    # 更新设置项 site_logo（存相对路径，域名无关，前后端同源可直接用）
    update_settings_svc(db, "general", {"site_logo": url})
    audit_log(db, user.id, "settings.logo_upload", "setting", "site_logo", {"url": url})
    return {"url": url}


@router.get("/logo/{name}")
def get_logo(name: str):
    """公开读取 Logo 文件（无需登录，供登录/注册页与布局直接展示）。"""
    # 仅允许 logo 前缀的文件名，杜绝路径遍历
    base = os.path.basename(name)
    if not base.startswith("logo"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    target = (FILES_DIR / base).resolve()
    try:
        target.relative_to(FILES_DIR.resolve())
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found") from None
    if not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return FileResponse(target)


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
    "rank_visible": "排行榜对用户可见",
    "default_pass_score": "默认及格线",
    "default_exam_duration_min": "默认考试时长(分钟)",
    "max_questions_per_exam": "单场最大题数",
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
    "register_allowed_group_ids": "允许公开注册加入的分组ID",
    "register_allowed_email_suffixes": "允许注册邮箱后缀(逗号分隔,留空不限制)",
    "mail_tpl_register": "注册验证码模板",
    "mail_tpl_exam_publish": "考试发布通知模板",
    "mail_tpl_review_done": "成绩公布通知模板",
}


def _label(key: str) -> str:
    return _LABELS.get(key, key)


def _value_type(key: str) -> str:
    if key in ("smtp_use_tls", "register_open", "new_user_need_approve", "register_group_required", "rank_visible"):
        return "bool"
    if key in (
        "default_pass_score",
        "default_exam_duration_min",
        "max_questions_per_exam",
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
            value = "******" if enc else v
            out.append({"key": k, "value": value, "encrypted": enc})
        else:
            _, cat, e = DEFAULT_SETTINGS.get(k, ("", "general", False))
            value = "******" if e else v
            out.append({"key": k, "value": value, "category": cat, "encrypted": e})
    return out


@router.put("/settings")
def update_settings(payload: SettingsUpdateIn, db: Session = Depends(get_db), user: User = Depends(require_super)):
    try:
        system_service.update_settings(db, payload.category, payload.updates)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    audit_log(db, user.id, "settings.update", "setting", payload.category, {"keys": list(payload.updates.keys())})
    return {"success": True}


@router.post("/smtp/test")
def smtp_test(
    payload: SmtpTestIn, bg: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_super)
):
    # 防滥用测试邮件发件：单管理员 5 次/小时
    from app.core.rate_limit import check

    check(f"smtp-test:user:{user.id}", 5, 3600, "SMTP 测试")
    settings = get_settings(db, "smtp")
    if not settings.get("smtp_host"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先配置 SMTP 服务器")
    bg.add_task(
        mail_service._send, payload.to_email, "培训考试平台 SMTP 测试", "这是一封测试邮件，SMTP 配置正常。", settings
    )
    return {"success": True, "message": "测试邮件已加入发送队列"}
