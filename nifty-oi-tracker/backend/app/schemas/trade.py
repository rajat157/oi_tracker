from datetime import datetime

from pydantic import BaseModel


class TradeBase(BaseModel):
    id: int
    created_at: datetime
    direction: str
    strike: int
    option_type: str
    entry_premium: float
    sl_premium: float
    spot_at_creation: float
    verdict_at_creation: str
    signal_confidence: float | None
    status: str
    resolved_at: datetime | None
    exit_premium: float | None
    exit_reason: str | None
    profit_loss_pct: float | None
    max_premium_reached: float | None
    min_premium_reached: float | None

    model_config = {"from_attributes": True}


class IronPulseTradeResponse(TradeBase):
    moneyness: str
    target1_premium: float
    target2_premium: float | None
    risk_pct: float
    hit_sl: bool
    hit_target: bool
    t1_hit: bool
    trailing_sl: float | None


class SellingTradeResponse(TradeBase):
    target_premium: float
    target2_premium: float | None
    t1_hit: bool
    t1_hit_at: datetime | None


class DessertTradeResponse(TradeBase):
    strategy_name: str
    target_premium: float
    iv_skew_at_creation: float | None
    vix_at_creation: float | None
    spot_move_30m: float | None


class MomentumTradeResponse(TradeBase):
    strategy_name: str
    target_premium: float
    combined_score: float | None
    confirmation_status: str | None


class TradeStats(BaseModel):
    total: int
    won: int
    lost: int
    win_rate: float
    avg_pnl: float
    total_pnl: float


class TradeListResponse(BaseModel):
    data: list[TradeBase]
    count: int
    strategy: str
