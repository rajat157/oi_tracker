from datetime import datetime

from pydantic import BaseModel


class AnalysisResponse(BaseModel):
    id: int
    timestamp: datetime
    spot_price: float
    atm_strike: int
    total_call_oi: int
    total_put_oi: int
    call_oi_change: int
    put_oi_change: int
    verdict: str
    prev_verdict: str | None
    vix: float
    iv_skew: float
    max_pain: int
    signal_confidence: float
    futures_oi: int
    futures_basis: float
    analysis_blob: dict | None

    model_config = {"from_attributes": True}


class AnalysisHistoryItem(BaseModel):
    timestamp: datetime
    spot_price: float
    verdict: str
    signal_confidence: float
    vix: float

    model_config = {"from_attributes": True}


class DashboardPayload(BaseModel):
    """Full dashboard response combining analysis + active trades + stats."""

    analysis: AnalysisResponse | None
    active_trades: dict  # strategy_name -> trade or None
    trade_stats: dict  # strategy_name -> stats
    chart_history: list[AnalysisHistoryItem]
