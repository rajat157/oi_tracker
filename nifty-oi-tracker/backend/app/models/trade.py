"""Trade models — 4 separate tables with a shared mixin."""

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TradeBaseMixin(TimestampMixin):
    """Shared columns across all trade strategy tables."""

    direction: Mapped[str] = mapped_column(String, nullable=False)
    strike: Mapped[int] = mapped_column(Integer, nullable=False)
    option_type: Mapped[str] = mapped_column(String, nullable=False)
    entry_premium: Mapped[float] = mapped_column(Float, nullable=False)
    sl_premium: Mapped[float] = mapped_column(Float, nullable=False)
    spot_at_creation: Mapped[float] = mapped_column(Float, nullable=False)
    verdict_at_creation: Mapped[str] = mapped_column(String, nullable=False)
    signal_confidence: Mapped[float | None] = mapped_column(Float)
    iv_at_creation: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String, nullable=False, default="ACTIVE")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_premium: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String)
    profit_loss_pct: Mapped[float | None] = mapped_column(Float)
    max_premium_reached: Mapped[float | None] = mapped_column(Float)
    min_premium_reached: Mapped[float | None] = mapped_column(Float)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_premium: Mapped[float | None] = mapped_column(Float)


class IronPulseTrade(TradeBaseMixin, Base):
    """Iron Pulse buying strategy (1:1 RR, formerly trade_setups)."""

    __tablename__ = "iron_pulse_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    moneyness: Mapped[str] = mapped_column(String, nullable=False)
    target1_premium: Mapped[float] = mapped_column(Float, nullable=False)
    target2_premium: Mapped[float | None] = mapped_column(Float)
    risk_pct: Mapped[float] = mapped_column(Float, nullable=False)
    expiry_date: Mapped[str] = mapped_column(String, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activation_premium: Mapped[float | None] = mapped_column(Float)
    hit_sl: Mapped[bool] = mapped_column(default=False)
    hit_target: Mapped[bool] = mapped_column(default=False)
    profit_loss_points: Mapped[float | None] = mapped_column(Float)
    # T1/T2 trailing stop
    t1_hit: Mapped[bool] = mapped_column(default=False)
    t1_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    t1_premium: Mapped[float | None] = mapped_column(Float)
    peak_premium: Mapped[float | None] = mapped_column(Float)
    trailing_sl: Mapped[float | None] = mapped_column(Float)
    # Technical context at creation
    call_oi_change_at_creation: Mapped[float] = mapped_column(Float, default=0.0)
    put_oi_change_at_creation: Mapped[float] = mapped_column(Float, default=0.0)
    pcr_at_creation: Mapped[float] = mapped_column(Float, default=0.0)
    max_pain_at_creation: Mapped[int] = mapped_column(Integer, default=0)
    support_at_creation: Mapped[int] = mapped_column(Integer, default=0)
    resistance_at_creation: Mapped[int] = mapped_column(Integer, default=0)
    trade_reasoning: Mapped[str] = mapped_column(String, default="")

    __table_args__ = (
        Index("ix_iron_pulse_status", "status"),
        Index("ix_iron_pulse_created", "created_at"),
    )


class SellingTrade(TradeBaseMixin, Base):
    """Options selling strategy (dual T1/T2)."""

    __tablename__ = "selling_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_premium: Mapped[float] = mapped_column(Float, nullable=False)
    target2_premium: Mapped[float | None] = mapped_column(Float)
    t1_hit: Mapped[bool] = mapped_column(default=False)
    t1_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_selling_status", "status"),
        Index("ix_selling_created", "created_at"),
    )


class DessertTrade(TradeBaseMixin, Base):
    """Premium 1:2 RR buying (Contra Sniper + Phantom PUT)."""

    __tablename__ = "dessert_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String, nullable=False)
    target_premium: Mapped[float] = mapped_column(Float, nullable=False)
    iv_skew_at_creation: Mapped[float | None] = mapped_column(Float)
    vix_at_creation: Mapped[float | None] = mapped_column(Float)
    max_pain_at_creation: Mapped[float | None] = mapped_column(Float)
    spot_move_30m: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        Index("ix_dessert_status", "status"),
        Index("ix_dessert_created", "created_at"),
    )


class MomentumTrade(TradeBaseMixin, Base):
    """Momentum trend-following strategy (1:2 RR)."""

    __tablename__ = "momentum_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String, default="Momentum")
    target_premium: Mapped[float] = mapped_column(Float, nullable=False)
    iv_skew_at_creation: Mapped[float | None] = mapped_column(Float)
    vix_at_creation: Mapped[float | None] = mapped_column(Float)
    combined_score: Mapped[float | None] = mapped_column(Float)
    confirmation_status: Mapped[str | None] = mapped_column(String)

    __table_args__ = (
        Index("ix_momentum_status", "status"),
        Index("ix_momentum_created", "created_at"),
    )
