"""公共列 Mixin。

继承自 Base 并标记 __abstract__，子类无需再显式继承 Base。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PKMixin(Base):
    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)


class TimestampMixin(Base):
    __abstract__ = True

    created_at: Mapped[str] = mapped_column(String, default=_now, nullable=False)
