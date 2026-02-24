from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AnalysisHistory(Base):
    __tablename__ = "analysis_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    spot_price: Mapped[float] = mapped_column(Float, nullable=False)
    atm_strike: Mapped[int] = mapped_column(Integer, nullable=False)
    total_call_oi: Mapped[int] = mapped_column(Integer, default=0)
    total_put_oi: Mapped[int] = mapped_column(Integer, default=0)
    call_oi_change: Mapped[int] = mapped_column(Integer, default=0)
    put_oi_change: Mapped[int] = mapped_column(Integer, default=0)
    atm_call_oi_change: Mapped[int] = mapped_column(Integer, default=0)
    atm_put_oi_change: Mapped[int] = mapped_column(Integer, default=0)
    itm_call_oi_change: Mapped[int] = mapped_column(Integer, default=0)
    itm_put_oi_change: Mapped[int] = mapped_column(Integer, default=0)
    verdict: Mapped[str] = mapped_column(String, nullable=False)
    prev_verdict: Mapped[str | None] = mapped_column(String)
    expiry_date: Mapped[str] = mapped_column(String, nullable=False)
    vix: Mapped[float] = mapped_column(Float, default=0.0)
    iv_skew: Mapped[float] = mapped_column(Float, default=0.0)
    max_pain: Mapped[int] = mapped_column(Integer, default=0)
    signal_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    futures_oi: Mapped[int] = mapped_column(Integer, default=0)
    futures_oi_change: Mapped[int] = mapped_column(Integer, default=0)
    futures_basis: Mapped[float] = mapped_column(Float, default=0.0)
    analysis_blob: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (Index("ix_analysis_history_timestamp", "timestamp"),)
