from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SystemLog(Base):
    __tablename__ = "system_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    level: Mapped[str] = mapped_column(String, nullable=False)
    component: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB)
    session_id: Mapped[str | None] = mapped_column(String)

    __table_args__ = (
        Index("ix_system_logs_timestamp", "timestamp"),
        Index("ix_system_logs_level", "level"),
        Index("ix_system_logs_component", "component"),
    )
