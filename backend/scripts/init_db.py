"""初始化数据库：建表、写入默认设置、创建超级管理员。

用法：uv run python scripts/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD
from app.core.security import hash_password
from app.database import db_session, init_db
from app.models.user import User
from app.services.system_service import ensure_defaults
from sqlalchemy import select


def main() -> None:
    init_db()
    with db_session() as db:
        ensure_defaults(db)
        existing = db.execute(select(User).where(User.email == SUPER_ADMIN_EMAIL)).scalar_one_or_none()
        if existing is None:
            db.add(User(
                email=SUPER_ADMIN_EMAIL,
                password_hash=hash_password(SUPER_ADMIN_PASSWORD),
                name="超级管理员",
                role="super_admin",
                status="active",
                email_verified=True,
            ))
            db.commit()
            print(f"[init] 超级管理员已创建: {SUPER_ADMIN_EMAIL}（密码经环境变量 TRAINING_SUPER_ADMIN_PASSWORD 注入，默认弱口令仅用于首次初始化）")
        else:
            print(f"[init] 超级管理员已存在: {SUPER_ADMIN_EMAIL}")
    print("[init] 数据库初始化完成 →", "data/quizhub.db")


if __name__ == "__main__":
    main()
