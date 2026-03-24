"""Shared trade monitoring prompt and validation for Claude active monitoring.

Used by RRAgent to evaluate active trades every
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

## STOP-LOSS ARCHITECTURE
- There is a HARD SL on the exchange (GTT) at the original SL level. This is disaster protection only and NEVER moves. Market makers can see this level.
- YOU manage a SOFT SL (trailing stop) which is tracked internally only. Market makers CANNOT see it.
- When you say TIGHTEN_SL, it ONLY updates the internal soft SL. The exchange order is NOT modified.
- If the soft SL was breached between cycles, you will be told. Evaluate whether it was:
  - A WICK (premium touched the level but recovered) → HOLD, the position is fine
  - A GENUINE BREAKDOWN (premium broke through and stayed down) → EXIT_NOW
- A wick that touches your level and bounces back is NOT a reason to exit — it's market noise or SL hunting.
- Consider: Has the premium recovered? Is it above VWAP? Is the trend still intact?

## DECISION RULES
- **HOLD**: Position is healthy, trend intact, no reason to change SL
- **TIGHTEN_SL**: Position is profitable and showing signs of weakening. Move soft SL to lock in gains at a technical level. New SL must be ABOVE current soft SL (never widen).
- **EXIT_NOW**: Clear reversal signal — broke support, VWAP breakdown with volume, momentum shift. Or soft SL breached AND premium has NOT recovered (genuine breakdown).

## IMPORTANT
- If in doubt, HOLD. Do not over-manage. Let winners run.
- Only TIGHTEN_SL when there is a clear technical level to move SL to.
- EXIT_NOW is for clear danger signals only, not minor pullbacks or wicks.
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
    hard_sl = trade_context.get("sl_premium", 0)
    soft_sl = trade_context.get("soft_sl_premium", 0)
    target = trade_context.get("target_premium", 0)
    option_type = trade_context.get("option_type", "?")
    strike = trade_context.get("strike", 0)
    elapsed_min = trade_context.get("time_in_trade_min", 0)
    max_prem = trade_context.get("max_premium_reached", current)

    soft_sl_breached = trade_context.get("soft_sl_breached", False)
    breach_premium = trade_context.get("soft_sl_breach_premium", 0)

    spot = analysis_context.get("spot_price", 0)
    vix = analysis_context.get("vix", 0) or 0
    verdict = analysis_context.get("verdict", "N/A")
    now = datetime.now()

    hard_sl_pct = (entry - hard_sl) / entry * 100 if entry > 0 else 0
    tgt_pct = (target - entry) / entry * 100 if entry > 0 else 0
    soft_sl_str = f"Rs {soft_sl:.2f}" if soft_sl > 0 else "Not set yet (you should set one)"

    # Breach alert section
    breach_section = ""
    if soft_sl_breached and soft_sl > 0:
        recovered = current > soft_sl
        breach_section = (
            f"\n## !! SOFT SL BREACH ALERT !!\n"
            f"- Soft SL at Rs {soft_sl:.2f} was breached between cycles\n"
            f"- Lowest premium during breach: Rs {breach_premium:.2f}\n"
            f"- Current premium: Rs {current:.2f} — {'RECOVERED above soft SL' if recovered else 'STILL BELOW soft SL'}\n"
            f"- IS THIS A WICK OR GENUINE BREAKDOWN? Check the chart.\n"
        )

    return f"""{MONITOR_SYSTEM_PROMPT}

## ACTIVE TRADE
- Strike: {strike} {option_type}
- Entry: Rs {entry:.2f}
- Current: Rs {current:.2f}
- P&L: {pnl_pct:+.2f}%
- Hard SL (exchange, fixed): Rs {hard_sl:.2f} (-{hard_sl_pct:.1f}% from entry)
- Soft SL (your trailing, internal): {soft_sl_str}
- Target: Rs {target:.2f} (+{tgt_pct:.1f}% from entry)
- Time in trade: {elapsed_min:.0f} min
- Max premium reached: Rs {max_prem:.2f}
{breach_section}
## CURRENT MARKET CONTEXT
- Spot Price: {spot:.2f}
- VIX: {vix:.1f}
- OI Verdict: {verdict}
- Time: {now.strftime('%H:%M IST')}

{chart_text}

## YOUR TASK
Evaluate the active trade above using the premium chart. Decide:
- HOLD if the trade looks healthy
- TIGHTEN_SL if profitable but weakening (provide new soft SL at a technical level)
- EXIT_NOW if clear reversal or confirmed soft SL breakdown (explain why)

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
        # Compare against soft_sl if set, otherwise hard sl
        current_sl = trade_context.get("soft_sl_premium", 0) or trade_context.get("sl_premium", 0)
        current_premium = trade_context.get("current_premium", 0)
        # New SL must be above current SL (never widen)
        if new_sl <= current_sl:
            return False
        # New SL must be below current premium (can't be above market)
        if current_premium > 0 and new_sl >= current_premium:
            return False
        return True

    return False
