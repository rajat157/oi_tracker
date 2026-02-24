"""Factory Boy factories for test data generation."""

from datetime import datetime, timezone

# Factory classes will be added as models are implemented.
# Placeholder for now.


def make_analysis_data(**overrides) -> dict:
    """Create a dict of analysis data for testing."""
    defaults = {
        "spot_price": 22500.0,
        "atm_strike": 22500,
        "total_call_oi": 5000000,
        "total_put_oi": 6000000,
        "call_oi_change": 100000,
        "put_oi_change": 150000,
        "verdict": "Slightly Bullish",
        "prev_verdict": "Neutral",
        "expiry_date": "2026-02-26",
        "vix": 14.5,
        "iv_skew": -0.5,
        "max_pain": 22400,
        "signal_confidence": 72.0,
        "futures_oi": 10000000,
        "futures_oi_change": 50000,
        "futures_basis": 45.0,
    }
    defaults.update(overrides)
    return defaults


def make_trade_data(strategy: str = "iron_pulse", **overrides) -> dict:
    """Create a dict of trade data for testing."""
    defaults = {
        "direction": "BUY_CALL",
        "strike": 22500,
        "option_type": "CE",
        "entry_premium": 150.0,
        "sl_premium": 120.0,
        "spot_at_creation": 22500.0,
        "verdict_at_creation": "Slightly Bullish",
        "signal_confidence": 72.0,
        "status": "ACTIVE",
    }
    if strategy == "iron_pulse":
        defaults.update(
            {
                "moneyness": "ATM",
                "target1_premium": 183.0,
                "risk_pct": 20.0,
                "expiry_date": "2026-02-26",
            }
        )
    elif strategy == "selling":
        defaults.update(
            {
                "direction": "SELL_CALL",
                "target_premium": 112.5,
                "target2_premium": 75.0,
            }
        )
    elif strategy == "dessert":
        defaults.update(
            {
                "strategy_name": "Contra Sniper",
                "target_premium": 225.0,
            }
        )
    elif strategy == "momentum":
        defaults.update(
            {
                "strategy_name": "Momentum",
                "target_premium": 225.0,
                "combined_score": 85.0,
            }
        )
    defaults.update(overrides)
    return defaults
