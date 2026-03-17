"""Shared trade monitoring prompt and validation for Claude active monitoring.

Used by both ScalperAgent and RRAgent to evaluate active trades every
3-min cycle. Claude can HOLD, TIGHTEN_SL, or EXIT_NOW.
"""

from datetime import datetime
from typing import Dict

MONITOR_SYSTEM_PROMPT = """You are an expert NIFTY FNO (Futures & Options) trade manager. You are monitoring an active LONG position and must decide whether to HOLD, TIGHTEN the stop-loss, or EXIT immediately.

## YOUR ANALYSIS FRAMEWORK
1. **Premium trend**: Is the premium strengthening or weakening since entry?
2. **VWAP relationship**: Is premium above VWAP (healthy) or breaking below (danger)?
3. **Support/Resistance**: Is premium approaching resistance (tighten SL) or breaking support (exit)?
4. **Momentum**: Are recent candles showing continuation or reversal?
5. **IV**: Expanding IV supports the position. Collapsing IV means exit.
6. **Time decay**: Options lose value as the day progresses. Factor time remaining.

## DECISION RULES
- **HOLD**: Position is healthy, trend intact, no reason to change SL/target
- **TIGHTEN_SL**: Position is profitable and showing signs of weakening (e.g., lower highs, VWAP rejection). Move SL to lock in partial gains. New SL must be ABOVE current SL (never widen it).
- **EXIT_NOW**: Clear reversal signal — broke support, VWAP breakdown with volume, momentum shift. Exit at market to avoid further loss.

## IMPORTANT
- If in doubt, HOLD. Do not over-manage.
- Only TIGHTEN_SL when there is a clear technical level to move SL to.
- EXIT_NOW is for clear danger signals only, not minor pullbacks.
- Never suggest widening the stop-loss.
- New SL premium must be at 0.05 tick increments (e.g. 195.05, 200.10)."""


def build_monitor_prompt(
    chart_text: str,
    trade_context: Dict,
    analysis_context: Dict,
) -> str:
    """Build the monitoring prompt for Claude active trade evaluation."""
    entry = trade_context.get("entry_premium", 0)
    current = trade_context.get("current_premium", 0)
    pnl_pct = trade_context.get("pnl_pct", 0)
    sl = trade_context.get("sl_premium", 0)
    target = trade_context.get("target_premium", 0)
    trail_stage = trade_context.get("trail_stage", 0)
    option_type = trade_context.get("option_type", "?")
    strike = trade_context.get("strike", 0)
    elapsed_min = trade_context.get("time_in_trade_min", 0)

    spot = analysis_context.get("spot_price", 0)
    vix = analysis_context.get("vix", 0) or 0
    verdict = analysis_context.get("verdict", "N/A")
    now = datetime.now()

    sl_pct = (entry - sl) / entry * 100 if entry > 0 else 0
    tgt_pct = (target - entry) / entry * 100 if entry > 0 else 0

    return f"""{MONITOR_SYSTEM_PROMPT}

## ACTIVE TRADE
- Strike: {strike} {option_type}
- Entry: Rs {entry:.2f}
- Current: Rs {current:.2f}
- P&L: {pnl_pct:+.2f}%
- SL: Rs {sl:.2f} (-{sl_pct:.1f}% from entry)
- Target: Rs {target:.2f} (+{tgt_pct:.1f}% from entry)
- Trail Stage: {trail_stage}
- Time in trade: {elapsed_min:.0f} min
- Max premium reached: Rs {trade_context.get('max_premium_reached', current):.2f}

## CURRENT MARKET CONTEXT
- Spot Price: {spot:.2f}
- VIX: {vix:.1f}
- OI Verdict: {verdict}
- Time: {now.strftime('%H:%M IST')}

{chart_text}

## YOUR TASK
Evaluate the active trade above using the premium chart. Decide:
- HOLD if the trade looks healthy
- TIGHTEN_SL if profitable but weakening (provide new SL at a technical level)
- EXIT_NOW if clear reversal (explain why)

Respond with ONLY valid JSON (no markdown, no explanation outside the JSON):
{{"action": "HOLD" or "TIGHTEN_SL" or "EXIT_NOW", "new_sl_premium": <float if TIGHTEN_SL>, "reasoning": "<brief explanation>"}}"""


def validate_monitor_response(response: Dict, trade_context: Dict) -> bool:
    """Validate a monitoring response from Claude.

    Returns True if the response is valid and actionable.
    """
    action = response.get("action", "")

    if action not in ("HOLD", "TIGHTEN_SL", "EXIT_NOW"):
        return False

    if action == "HOLD":
        return True

    if action == "EXIT_NOW":
        return True

    if action == "TIGHTEN_SL":
        new_sl = response.get("new_sl_premium")
        if not isinstance(new_sl, (int, float)) or new_sl <= 0:
            return False
        current_sl = trade_context.get("sl_premium", 0)
        current_premium = trade_context.get("current_premium", 0)
        # New SL must be above current SL (never widen)
        if new_sl <= current_sl:
            return False
        # New SL must be below current premium (can't be above market)
        if current_premium > 0 and new_sl >= current_premium:
            return False
        return True

    return False
