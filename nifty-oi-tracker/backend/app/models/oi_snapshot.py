from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OISnapshot(Base):
    __tablename__ = "oi_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    spot_price: Mapped[float] = mapped_column(Float, nullable=False)
    strike_price: Mapped[int] = mapped_column(Integer, nullable=False)
    ce_oi: Mapped[int] = mapped_column(Integer, default=0)
    ce_oi_change: Mapped[int] = mapped_column(Integer, default=0)
    pe_oi: Mapped[int] = mapped_column(Integer, default=0)
    pe_oi_change: Mapped[int] = mapped_column(Integer, default=0)
    ce_volume: Mapped[int] = mapped_column(BigInteger, default=0)
    pe_volume: Mapped[int] = mapped_column(BigInteger, default=0)
    ce_iv: Mapped[float] = mapped_column(Float, default=0.0)
    pe_iv: Mapped[float] = mapped_column(Float, default=0.0)
    ce_ltp: Mapped[float] = mapped_column(Float, default=0.0)
    pe_ltp: Mapped[float] = mapped_column(Float, default=0.0)
    expiry_date: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        Index("ix_oi_snapshots_ts_strike", "timestamp", "strike_price"),
        Index("ix_oi_snapshots_timestamp", "timestamp"),
    )
