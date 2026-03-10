"""Core analysis result data structure wrapping the tug-of-war output."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class AnalysisResult:
    """Structured wrapper around the dict returned by analyze_tug_of_war().

    Provides typed attribute access while remaining backward-compatible
    with dict-based code via to_dict() / from_dict().
    """

    # --- Primary fields ---
    spot_price: float = 0.0
    atm_strike: int = 0
    verdict: str = "No Data"
    strength: str = ""
    combined_score: float = 0.0
    smoothed_score: float = 0.0
    signal_confidence: float = 0.0

    # --- OI totals ---
    total_call_oi: int = 0
    total_put_oi: int = 0
    call_oi_change: float = 0.0
    put_oi_change: float = 0.0
    net_oi_change: float = 0.0
    pcr: float = 0.0

    # --- Volume ---
    total_call_volume: int = 0
    total_put_volume: int = 0
    volume_pcr: float = 0.0

    # --- Conviction ---
    avg_call_conviction: float = 0.0
    avg_put_conviction: float = 0.0

    # --- Momentum & price ---
    momentum_score: float = 0.0
    price_change_pct: float = 0.0

    # --- Zone scores ---
    below_spot_score: float = 0.0
    above_spot_score: float = 0.0

    # --- Confirmation ---
    confirmation_status: str = "NEUTRAL"
    confirmation_message: str = ""

    # --- IV / VIX / Max Pain ---
    iv_skew: float = 0.0
    max_pain: int = 0
    vix: float = 0.0

    # --- Futures ---
    futures_oi_change: float = 0.0
    is_diverging: bool = False

    # --- Two-candle ---
    two_candle_confirmed: bool = False

    # --- Nested structures (kept as dicts for flexibility) ---
    otm_puts: Dict[str, Any] = field(default_factory=dict)
    itm_calls: Dict[str, Any] = field(default_factory=dict)
    otm_calls: Dict[str, Any] = field(default_factory=dict)
    itm_puts: Dict[str, Any] = field(default_factory=dict)
    strength_analysis: Dict[str, Any] = field(default_factory=dict)
    below_spot: Dict[str, Any] = field(default_factory=dict)
    above_spot: Dict[str, Any] = field(default_factory=dict)
    weights: Dict[str, Any] = field(default_factory=dict)
    oi_clusters: Any = None
    trade_setup: Dict[str, Any] = field(default_factory=dict)
    trap_warning: Any = None
    oi_acceleration: Dict[str, Any] = field(default_factory=dict)
    premium_momentum: Dict[str, Any] = field(default_factory=dict)
    market_regime: Any = None
    primary_sr: Dict[str, Any] = field(default_factory=dict)
    oi_flow_summary: Dict[str, Any] = field(default_factory=dict)

    # --- Catch-all for any extra keys (forward-compat) ---
    _extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    # --- Error field ---
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert back to the raw dict format used by existing code."""
        d: dict = {}
        for fld in self.__dataclass_fields__:
            if fld == "_extra":
                continue
            d[fld] = getattr(self, fld)
        d.update(self._extra)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> AnalysisResult:
        """Build from the raw dict returned by analyze_tug_of_war()."""
        if d is None:
            return cls()
        known = set(cls.__dataclass_fields__) - {"_extra"}
        kwargs = {}
        extra = {}
        for k, v in d.items():
            if k in known:
                kwargs[k] = v
            else:
                extra[k] = v
        obj = cls(**kwargs)
        obj._extra = extra
        return obj

    def __getitem__(self, key: str) -> Any:
        """Allow dict-style access for backward compatibility."""
        if key in self.__dataclass_fields__ and key != "_extra":
            return getattr(self, key)
        return self._extra[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-compatible get()."""
        try:
            return self[key]
        except (KeyError, AttributeError):
            return default
