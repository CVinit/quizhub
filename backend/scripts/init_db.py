"""初始化数据库：建表、写入默认设置、创建超级管理员。

用法：uv run python scripts/init_db.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import DB_PATH, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD, SUPER_ADMIN_PASSWORD_IS_DEFAULT
from app.core.security import hash_password
from app.database import db_session, init_db
from app.models.user import User
from app.services.system_service import ensure_defaults

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("quizhub")


def main() -> None:
    init_db()
    with db_session() as db:
        ensure_defaults(db)
        existing = db.execute(select(User).where(User.email == SUPER_ADMIN_EMAIL)).scalar_one_or_none()
        if existing is not None:
            logger.info("[init] 超级管理员已存在: %s（口令保持不变）", SUPER_ADMIN_EMAIL)
        else:
            # 仅在需要新建超管时生成/使用口令；已存在时不应打印误导性的随机口令。
            admin_password = SUPER_ADMIN_PASSWORD
            if SUPER_ADMIN_PASSWORD_IS_DEFAULT:
                import secrets
                import string

                alphabet = string.ascii_letters + string.digits
                admin_password = "".join(secrets.choice(alphabet) for _ in range(16))
                logger.warning(
                    "[init] 未设置 TRAINING_SUPER_ADMIN_PASSWORD，已生成一次性随机超管口令：\n"
                    "       %s\n"
                    "       请立即记录并首次登录后修改。生产环境务必经环境变量注入固定口令。",
                    admin_password,
                )
            db.add(
                User(
                    email=SUPER_ADMIN_EMAIL,
                    password_hash=hash_password(admin_password),
                    name="超级管理员",
                    role="super_admin",
                    status="active",
                    email_verified=True,
                )
            )
            db.commit()
            logger.info("[init] 超级管理员已创建: %s", SUPER_ADMIN_EMAIL)
    logger.info("[init] 数据库初始化完成 → %s", DB_PATH)


if __name__ == "__main__":
    main()
