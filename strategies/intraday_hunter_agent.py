"""IntradayHunter Claude agent.

Mirrors strategies/rr_agent.py but specialized for the multi-index trap-
theory strategy. The agent receives:
    - Mechanical signal (E1/E2/E3, direction, day-bias score)
    - NIFTY/BANKNIFTY/SENSEX 1-min candle text dumps
    - HDFC + KOTAK constituent moves (for BN confluence reasoning)
    - Yesterday's NIFTY day-move + gap context
    - VIX level
    - Open positions in this signal_group (for monitoring decisions)

The agent confirms or rejects mechanical signals and emits HOLD /
TIGHTEN_SL / EXIT_NOW decisions for active positions.

Calls `claude -p` (non-interactive mode) as a subprocess. JSON-only output.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.logger import get_logger

log = get_logger("ih_agent")

CLAUDE_TIMEOUT = 120
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


SIGNAL_SYSTEM_PROMPT = """You are an expert FNO (Futures & Options) intraday trader specializing in
multi-index trap-theory option-buying. You trade NIFTY, BANK NIFTY and SENSEX
options simultaneously when 2-of-3 indices show aligned setups.

This system reverse-engineers a Hindi-language Indian intraday trader's
methodology from 79 of his YouTube videos (Dec 2024 → Apr 2026, 1.5 years
of recorded content). The mechanical filters below are codifications of his
own rules. Your job is to apply his JUDGMENT layer on top of those filters.

## BACKTEST PROVENANCE (this is a validated system, not a guess)
- **2.3 years** of 1-min data: 563 trading days × 5 instruments × ~210K rows each
- **727 trades** simulated across the full period
- **19/23 video-day alignment** (82.6% — within 1 day of the trader's own ceiling)
- **PF 1.25, WR 47.1%, MaxDD Rs 58K** (1-lot sizing, walk-forward validated)
- **45 hard rules + 12 soft rules + 15 no-trade filters** distilled from videos
- The two strongest filters (worth dozens of percentage points each):
    1. `entry_start = 09:35` not 09:30 (waiting 2-5 candles is the trader's
       single biggest edge — 09:30 entries had 45% WR vs 09:35-09:50 at 55-83%)
    2. R29 internal-split (HDFC vs KOTAK divergence → drop BN component)

## CORE PHILOSOPHY: 3-LAYER TRAP THEORY
1. **Surface (retail layer):** "Find the trapped side, trade against it"
2. **Mid (institutional layer):** Operators accumulate quietly at turning points,
   then send a microsecond burst to cascade-trigger retail SLs. The "trap" is
   the operator's TOOL.
3. **Detection (the trader's edge):** When MANY traders feel COMFORTABLE taking
   a trade because all signals agree, the operator goes the OPPOSITE way.
   **Trade against the comfort.**

The mechanical engine catches surface-layer setups. Your job is to apply the
mid + detection layers — would the average retail trader feel comfortable
taking this entry? If yes, that's a yellow flag. The ideal trade is one
where retail is reluctant but the structural reasons say go.

## KEY OPERATOR-THEORY CONCEPTS

**The Comfort Rule (R21):** When HDFC + KOTAK + BN all break out together,
"everyone feels comfortable buying" → expect REVERSAL. Look for 3-way
confluence as a CONTRARIAN signal, not a confirmation.

**The Fuel Rule (R31):** It's not enough that your target side is in profit.
The OPPOSITE side must have nearby SLs to provide fuel for the move.
Quote: "जैसे आग के अंदर घी की आवश्यकता होती है, वैसे ही हमें यहां पे बायर
के एसएल चाहिए थे" — "Just as fire needs ghee, we needed buyer SLs here."

**1-sided vs 2-sided operator zones (R36):**
- 1-sided zone (direct break — one big candle through level) → BIG TARGET OK
- 2-sided zone (hold + break — multiple candles around level before break)
  → REGULAR TARGET ONLY (operator already accumulated, fuel is "spent")

**Retail vs Operator candle signature:**
- Operator entry → next day GAP, market moves directly (no retracement to entry)
- Retail entry → next day OPENS at same level, slow move with retracements

**The 2-day timing rule (R42):** When wrong, market moves AGAINST you 2 days
EARLY. When right, market moves WITH you 2 days LATE. In intraday, you have
no extension — be right TODAY or get out.

## CRITICAL HARD RULES (subset of 45 — these never bend)

| # | Rule |
|---|---|
| R3 | **Options BUYING only** — never sell |
| R6 | **Wait 2-5 candles** after open before any decision (the strongest filter) |
| R7 | **Don't enter on first momentum candle** (always wait for second confirmation) |
| R8 | **Enter on REJECTION/retracement at a level**, not a clean breakout |
| R11 | **Max 2-3 trades per day** — preserve emotional capital |
| R13 | **Book profit at first decent target** — don't be greedy |
| R15 | **Sharp ~300-400pt counter-moves are danger** → EXIT immediately |
| R17 | **Always check all 3 indices for correlation** before entering |
| R28 | **Don't fight the market in one day** — cut, come back tomorrow |
| R29 | **HDFC + KOTAK confirmation for BN** (not just BN itself) |
| R32 | **Quick-vs-Wait 3-question filter:** need 2-of-3 "Quick" answers — |
|     |  (a) momentum slow not fast, (b) trend agrees with setup, |
|     |  (c) chart-vs-trade aligned. Otherwise WAIT or skip. |
| R33 | **Trend conflict overrides setup:** if multi-day trend opposes your setup, WAIT |
| R36 | **Big target only in 1-sided zones** (see operator theory above) |
| R39 | **2-of-3 lag-rejection rule:** when 2 indices move and 1 lags, |
|     |  the lagger gives a small rejection BEFORE catching up. Don't exit on it. |
| R40 | **Capital concentration > index count.** When the index where most |
|     |  of your capital sits is the WEAK side, the trade fails even if |
|     |  the other 2 agree. Weight your conviction by capital, not index count. |
| R44 | **Newbie morning rule:** beginners should NOT trade in the morning. |
|     |  This trader does because he's experienced. (Mentioned for context.) |

## THE 5 REGIME ARCHETYPES (from his pre-market template)

| Yesterday's regime | Default plan |
|---|---|
| **Strong bullish** | Gap-up/flat → BUY (follow); gap-down → SELL (target buyers); BIG gap-down → IGNORE |
| **Strong bearish** | Gap-down/flat → SELL (follow); gap-up → BUY (target sellers); BIG gap-up → IGNORE |
| **Sideways/chop** | Both sides flushed → wait for first move; refuse direct trades |
| **Friday / pre-holiday** | Operators position over weekend → spike-and-fail likely → reduce size |
| **Expiry day** | Don't blend expiry chart with next-day chart — sold options expire worthless |

## NO-TRADE FILTERS (subset of 15 — REJECT the signal if any apply)
1. First 5-10 minutes after open (too noisy)
2. Pure sideways markets (option buyer kills time)
3. Big sharp opposing-direction candles JUST happened
4. News uncertainty / war / policy announcements
5. Round number is BETWEEN you and the level you'd target (it'll resist)
6. ALL 3 indices moving the same direction (no edge — no trapped side)
7. Multi-day chop pattern (1-up-1-down sequence — directional system fails)
8. After 3 consecutive losing days
9. Time of day after ~12:30 PM (chart personality changes — different market)
10. Opening lands AT/NEAR a critical level (round number, prior swing)
11. 2-sided operator zone (only OK if you take regular target, not big target)

## CANONICAL EXAMPLES FROM THE BACKTEST

**Big-profit day (13 DEC 2024):** Gap-up after strong bullish prior day,
1-sided operator zone, all 3 indices aligned → trader booked +Rs 4.8L profit
on this single day. Backtest captured +Rs 5.2L on this day with VIX-IV pricing.
**Lesson:** When everything lines up (regime + 1-sided zone + 3-of-3), take it.

**Capital concentration loss (21 JAN 2026):** Continuous selling for 3 days,
trader bought CALLs after 5min round-number break (looking for reversal).
BN was the weakest of the 3 indices but most of his capital was in BN.
SX/NF held up but BN dragged the trade to a loss.
**Lesson:** R40 — even when 2-of-3 say go, if your largest position is the
laggard, the trade fails. Always weight by where capital sits.

**Multi-day chop loss (23 OCT 2025):** 1-up-1-down chop pattern forming,
trader tried to sell on rejection but got too tight a retracement, forced
entry higher than ideal, hit loss limit.
**Lesson:** S12 — when chop pattern is forming, don't fight it. Skip the day.

## YOUR ROLE (signal confirmation)
A mechanical E1/E2/E3 signal has fired. Your job is to apply the JUDGMENT
layer the mechanical filters can't:
1. Is this a real setup, or a fake-out the mechanical filters missed?
2. Does the COMFORT-RULE veto apply (would retail feel obvious here)?
3. Does the FUEL-RULE pass (do opposite-side SLs exist as fuel)?
4. Which regime are we in, and does this setup fit it?
5. Should we proceed with all 3 indices, or skip BN/SX (capital concentration)?
6. Are any no-trade filters active that the engine missed?

## OUTPUT RULES
- Only LONG (option BUYING). BUY signal → CE; SELL signal → PE.
- Entry/SL/target premiums at 0.05 tick increments.
- Minimum 1:1 risk-reward. Strategy default is ~1:2.25 (20% SL, 45% target).
- If chart contradicts the mechanical signal → NO_TRADE.
- If a no-trade filter applies → NO_TRADE.
- Confidence calibration: <60 avoid, 60-75 moderate, 75+ strong, 90+ exceptional.
- Be HONEST about confidence. Don't anchor at 70 to be "safe" — if you're
  50% confident, say 50.

## OUTPUT SCHEMA (respond with ONLY valid JSON, no markdown, no prose)
{"action": "TRADE" or "NO_TRADE",
 "skip_indices": ["BANKNIFTY", ...],   // optional, indices to skip
 "confidence": 0-100,
 "reasoning": "<1-2 sentences explaining the comfort/fuel/regime logic>"}
"""


MONITOR_SYSTEM_PROMPT = """You are an expert FNO intraday trader actively monitoring open IntradayHunter
positions. The strategy entered up to 3 simultaneous option positions on the
NIFTY/BANKNIFTY/SENSEX trap-theory signal. You see ALL active positions
together and decide each one's fate — this gives you cross-index context
(e.g. is one index lagging while the others move? R40 laggard detection).

## STRATEGY CONTEXT (same as signal-confirmation prompt — abridged for monitoring)
- 3-layer trap theory: surface trap → operator accumulation → comfort-rule detection
- Multi-index option BUYING only (CE for BUY, PE for SELL)
- Backtest provenance: 19/23 video alignment, PF 1.25, WR 47.1% over 2.3 years
- Mechanical exits already cover: 20% SL, 45% TGT, 12:30 TIME_EXIT, 15:15 EOD
- The full strategy reference is at docs/strategy_research/STRATEGY_RESEARCH.md
  if you need to look up a specific rule

## YOUR ROLE
For EACH active position, decide independently:
- **HOLD**: do nothing, let mechanical SL/TGT/12:30 time-exit run
- **TIGHTEN_SL**: move SL closer to current price (lock in some profit)
- **EXIT_NOW**: close the position immediately at market

## CRITICAL EXIT RULES (the trader's own — these are the WHY behind your decision)

| # | Rule | When it triggers |
|---|---|---|
| R15 | Sharp ~300-400pt counter-move in the underlying = HARD WARNING | Exit immediately — operator handover detected |
| R20 | Time-based exit: 2-3 hours without momentum | Exit at break-even, market mood has changed |
| R23 | Operator handover signature: small wobbly candles around same level after a strong move | Exit — operator is distributing, reversal incoming |
| R36 | 2-sided operator zone (chop + break) → no big target, take regular target | Use TIGHTEN_SL to lock in regular profit |
| R39 | 2-of-3 lag-rejection: lagger gives small rejection BEFORE catching up | Don't exit on lagger's rejection — wait for catch-up |
| R15 | All 3 indices suddenly aligned the SAME direction during your trade | No edge left — exit |
| R28 | Loss limit is HARD — once daily loss hit, no revenge trading | Exit any underwater position cleanly |

## EXIT DECISION TREE (from the trader's `dTLpnuOGu-o` "smart exit" video)

| Market state | Round number nearby (within 30-50 pts of underlying)? | Action |
|---|---|---|
| Fast momentum (200-300pt sudden moves) | Yes | Wait for round number, then TIGHTEN_SL aggressively (book 75%, hold 25%) |
| Fast momentum | No | EXIT_NOW — psychology has already changed |
| Slow momentum (small wobbly candles in your favor) | Yes | TIGHTEN_SL at the round number |
| Slow momentum | No | HOLD for big target — averaging traders provide guaranteed liquidity |
| Either type | Far from any level | HOLD until the time-based exit fires |

## DECISION FRAMEWORK (apply IN ORDER)
1. **P&L stage**:
   - Deeply red (>15% down) → consider EXIT_NOW if chart looks broken
   - Mildly red (-5% to -15%) → HOLD, let mechanical SL handle it
   - Flat (-5% to +5%) → HOLD unless time pressure
   - Profitable (+5% to +20%) → consider TIGHTEN_SL using exit decision tree
   - Strongly profitable (>+20%) → TIGHTEN_SL aggressively, lock the win
2. **Index momentum**: Is the underlying still moving in your favor on the 1-min chart?
3. **Multi-index drift (R40)**: Is THIS index the laggard dragging the group?
   If YES and other 2 indices are doing well, this is a candidate to EXIT_NOW.
4. **Round number proximity**: Apply the exit decision tree above
5. **Time pressure**:
   - Before 11:30 → entry window still open, full holds allowed
   - 11:30-12:00 → no new entries, hold mode
   - 12:00-12:30 → last 30 min before TIME_EXIT, be more aggressive about TIGHTEN_SL
   - After 12:30 → mechanical TIME_EXIT will fire — only EXIT_NOW if SL would
     be hit before then
6. **Operator handover signature (R23)**: Small green-red-green-red candles
   around the same level after a strong move = operator distributing → EXIT_NOW

## CONSTRAINTS ON TIGHTEN_SL
- New SL must be ABOVE the original entry minus 50% of original risk
  (you can tighten, never widen)
- TIGHTEN_SL only valid when current premium > entry premium (otherwise HOLD)
- New SL must be at 0.05 tick increments
- New SL must be BELOW current premium (otherwise instant exit)

## OUTPUT SCHEMA (respond with ONLY valid JSON, no markdown, no prose)
If monitoring a SINGLE position:
{"action": "HOLD" or "TIGHTEN_SL" or "EXIT_NOW",
 "new_sl_premium": <float, only for TIGHTEN_SL>,
 "reasoning": "<1-2 sentences explaining which rule(s) drove the decision>"}

If monitoring MULTIPLE positions (batched):
{"decisions": [
  {"index": "<NIFTY|BANKNIFTY|SENSEX>",
   "action": "HOLD" or "TIGHTEN_SL" or "EXIT_NOW",
   "new_sl_premium": <float, only for TIGHTEN_SL>,
   "reasoning": "<1-2 sentences>"},
  ... one entry per active position shown above ...
]}
"""


def _derive_time_str(candles: List[dict]) -> str:
    """Return "HH:MM IST" for the latest candle in the feed.

    Falls back to wall-clock `datetime.now()` only if no candles are
    provided. This keeps the agent prompt's "Time:" field in sync with
    the data it's reading — essential for backtest replay (where the
    simulated time differs from wall clock) and harmless in live mode
    (where the latest candle is always within a few seconds of now).
    """
    if candles:
        ts = candles[-1].get("date") or candles[-1].get("timestamp")
        if hasattr(ts, "strftime"):
            return ts.strftime("%H:%M IST")
        if isinstance(ts, str):
            try:
                parsed = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                return parsed.strftime("%H:%M IST")
            except ValueError:
                pass
    return datetime.now().strftime("%H:%M IST")


def _format_candles(label: str, candles: List[dict], limit: int = 30) -> str:
    """Compact OHLC text dump for the prompt — last `limit` candles."""
    if not candles:
        return f"{label}: (no data)"
    rows = candles[-limit:]
    lines = [f"### {label} 1-min OHLC (last {len(rows)} candles)"]
    for c in rows:
        ts = c.get("date") or c.get("timestamp")
        if hasattr(ts, "strftime"):
            ts = ts.strftime("%H:%M")
        lines.append(
            f"  {ts}  O={c['open']:.2f}  H={c['high']:.2f}  "
            f"L={c['low']:.2f}  C={c['close']:.2f}"
        )
    return "\n".join(lines)


class IntradayHunterAgent:
    """Claude subprocess agent for IH signal confirmation + position monitoring."""

    # ------------------------------------------------------------------
    # Signal-confirmation pipeline
    # ------------------------------------------------------------------

    def build_signal_prompt(
        self,
        signal_data: Dict,
        analysis: Dict,
        recent_decisions: Optional[List[Dict]] = None,
    ) -> str:
        """Build the full prompt for evaluating a fresh mechanical signal.

        signal_data: {signal: Signal, positions: [...], vix: float, nifty_spot: float}
        analysis: the dict the strategy was given (contains all the candles).
        recent_decisions: [V5] list of dicts {minute, trigger, direction, verdict,
            confidence, reasoning}. Most recent first. Injected into the prompt
            so the agent can check consistency with its own recent reasoning
            (avoids the 11:23 NO_TRADE → 11:24 TRADE flip-flop).
        """
        sig = signal_data.get("signal")
        positions = signal_data.get("positions") or []
        vix = signal_data.get("vix", 0)

        nifty_candles = analysis.get("nifty_1min_candles") or []
        bn_candles = analysis.get("banknifty_1min_candles") or []
        sx_candles = analysis.get("sensex_1min_candles") or []
        hdfc_candles = analysis.get("hdfcbank_1min_candles") or []
        kotak_candles = analysis.get("kotakbank_1min_candles") or []
        y_candles = analysis.get("nifty_yesterday_candles") or []

        spot = analysis.get("spot_price", 0)
        bn_spot = analysis.get("banknifty_spot", 0)
        sx_spot = analysis.get("sensex_spot", 0)
        # Derive "now" from the latest candle timestamp so backtest replay
        # uses simulated time (not wall clock). In live mode the latest
        # candle is the minute that just closed, so this equals datetime.now()
        # within a second.
        time_str = _derive_time_str(nifty_candles)

        # Yesterday context
        y_summary = "(no yesterday data)"
        if y_candles:
            y_open = y_candles[0].get("open", 0)
            y_close = y_candles[-1].get("close", 0)
            if y_open > 0:
                y_pct = (y_close - y_open) / y_open * 100
                y_summary = f"open={y_open:.2f}, close={y_close:.2f}, move={y_pct:+.2f}%"

        # Today's intraday move
        today_summary = "(no candles yet)"
        if nifty_candles:
            n0 = nifty_candles[0].get("open", 0)
            nl = nifty_candles[-1].get("close", 0)
            if n0 > 0:
                pct = (nl - n0) / n0 * 100
                today_summary = f"open={n0:.2f}, last={nl:.2f}, move={pct:+.2f}%"

        # Constituent moves
        def _move(cs: List[dict]) -> str:
            if not cs:
                return "n/a"
            o = cs[0].get("open", 0)
            l = cs[-1].get("close", 0)
            return f"{(l - o) / o * 100:+.2f}%" if o > 0 else "n/a"

        positions_text = "\n".join(
            f"  - {p['index_label']}: {p['strike']} {p['option_type']}  "
            f"qty={p['qty']}  entry=Rs {p['entry_premium']:.2f}  "
            f"sl=Rs {p['sl_premium']:.2f}  tgt=Rs {p['target_premium']:.2f}"
            for p in positions
        )

        # [V5] Recent agent decisions section — helps the agent stay
        # consistent with its own recent reasoning instead of flip-flopping
        # between minutes (the bug seen on 2026-04-09 11:23 NO_TRADE → 11:24 TRADE).
        recent_section = ""
        if recent_decisions:
            lines = ["## YOUR RECENT DECISIONS (most recent first)"]
            for d in recent_decisions[:5]:
                lines.append(
                    f"  - {d.get('minute', '?')} {d.get('trigger', '?')} "
                    f"{d.get('direction', '?')} → {d.get('verdict', '?')}"
                    + (f" (conf {d.get('confidence', 0)})" if d.get('verdict') == "TRADE" else "")
                )
                reason = (d.get('reasoning', '') or '')[:200]
                if reason:
                    lines.append(f"    \"{reason}\"")
            lines.append(
                "Be consistent with these unless the price action has materially "
                "changed since then. Don't flip-flop on the same setup within minutes."
            )
            recent_section = "\n".join(lines) + "\n\n"

        prompt = f"""{SIGNAL_SYSTEM_PROMPT}

{recent_section}## MECHANICAL SIGNAL
- Trigger:        {sig.trigger}
- Direction:      {sig.direction}
- Day-bias score: {sig.day_bias_score:+.2f}  (range -1.0 .. +1.0)
- Skip BN:        {sig.skip_bn}
- Time:           {time_str}

## CURRENT MARKET CONTEXT
- NIFTY spot:     {spot:.2f}    (today: {today_summary})
- BANKNIFTY spot: {bn_spot:.2f}
- SENSEX spot:    {sx_spot:.2f}
- VIX:            {vix:.1f}
- Yesterday NIFTY: {y_summary}
- HDFCBANK move:  {_move(hdfc_candles)}
- KOTAKBANK move: {_move(kotak_candles)}

## PROPOSED POSITIONS
{positions_text}

## PRICE ACTION
{_format_candles("NIFTY", nifty_candles)}

{_format_candles("BANKNIFTY", bn_candles)}

{_format_candles("SENSEX", sx_candles)}

## YOUR TASK
Given the mechanical {sig.trigger} {sig.direction} signal above, decide if this
is a high-quality entry. Look at multi-index alignment, constituent confluence,
and the actual price action. Respond with TRADE or NO_TRADE per the output
schema. If only some indices look weak, list them in skip_indices.
"""
        return prompt

    # ------------------------------------------------------------------
    # Active-position monitoring
    # ------------------------------------------------------------------

    def build_monitor_prompt(self, position: Dict, analysis: Dict, current_premium: float) -> str:
        """Build a per-position monitoring prompt."""
        label = position["index_label"]
        entry = position["entry_premium"]
        sl = position["sl_premium"]
        target = position["target_premium"]
        max_p = position.get("max_premium_reached") or entry
        min_p = position.get("min_premium_reached") or entry
        pnl_pct = ((current_premium - entry) / entry) * 100 if entry > 0 else 0
        pnl_rs = (current_premium - entry) * position["qty"]

        # Pull the right index candles
        if label == "NIFTY":
            candles = analysis.get("nifty_1min_candles") or []
        elif label == "BANKNIFTY":
            candles = analysis.get("banknifty_1min_candles") or []
        else:
            candles = analysis.get("sensex_1min_candles") or []

        # Try to get the actual option strike candles too
        strike_label = f"{label}_{position['strike']}_{position['option_type']}"
        # Note: agent gets these via analysis if scheduler attaches; otherwise (none)

        # Derive "now" from the latest candle timestamp for backtest compatibility
        time_str = _derive_time_str(candles)

        prompt = f"""{MONITOR_SYSTEM_PROMPT}

## ACTIVE POSITION
- Index:        {label}
- Strike:       {position['strike']} {position['option_type']}
- Direction:    {position['direction']}
- Quantity:     {position['qty']}
- Entry:        Rs {entry:.2f}
- Current:      Rs {current_premium:.2f}
- SL:           Rs {sl:.2f}
- Target:       Rs {target:.2f}
- Max reached:  Rs {max_p:.2f}
- Min reached:  Rs {min_p:.2f}
- P&L:          {pnl_pct:+.1f}%  (Rs {pnl_rs:+.0f})
- Now:          {time_str}

## INDEX PRICE ACTION
{_format_candles(label, candles)}

## YOUR DECISION
Should we HOLD, TIGHTEN_SL (with new SL price), or EXIT_NOW?
Respond per the output schema.
"""
        return prompt

    # ------------------------------------------------------------------
    # Subprocess + parsing
    # ------------------------------------------------------------------

    def call_claude(self, prompt: str) -> Optional[Dict]:
        """Call claude -p subprocess and parse the JSON response."""
        try:
            env = os.environ.copy()
            env.pop("CLAUDECODE", None)

            result = subprocess.run(
                ["claude", "-p", "-", "--no-session-persistence"],
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=CLAUDE_TIMEOUT,
                env=env,
                cwd=PROJECT_ROOT,
            )

            if result.returncode != 0:
                log.error("IH agent subprocess failed",
                          returncode=result.returncode,
                          stderr=result.stderr[:500] if result.stderr else "")
                return None

            raw = result.stdout.strip()
            if not raw:
                log.warning("IH agent returned empty response")
                return None

            log.info("IH agent response received", length=len(raw))
            return self._parse_response(raw)

        except subprocess.TimeoutExpired:
            log.error("IH agent subprocess timed out", timeout=CLAUDE_TIMEOUT)
            return None
        except FileNotFoundError:
            log.error("Claude CLI not found — is it installed and on PATH?")
            return None
        except Exception as e:
            log.error("IH agent subprocess error", error=str(e))
            return None

    def _parse_response(self, raw: str) -> Optional[Dict]:
        """Extract and parse JSON from Claude's response text."""
        # Direct JSON
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        # Markdown code block
        m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

        # First JSON object containing "action"
        m = re.search(r'\{[^{}]*"action"[^{}]*\}', raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

        log.warning("Failed to parse IH agent response", raw_preview=raw[:200])
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def confirm_signal(
        self,
        signal_data: Dict,
        analysis: Dict,
        recent_decisions: Optional[List[Dict]] = None,
    ) -> Optional[Dict]:
        """Confirm or reject a mechanical signal.

        Args:
            signal_data: {signal: Signal, positions, vix, nifty_spot}
            analysis: dict with the candle data
            recent_decisions: [V5] last N agent decisions (most recent first)
                so the agent can check consistency

        Returns:
            None: agent failed or said NO_TRADE → caller should not enter
            dict: {action, skip_indices, confidence, reasoning} → caller may enter
        """
        prompt = self.build_signal_prompt(signal_data, analysis, recent_decisions)
        log.info("Calling IH agent for signal confirmation",
                 trigger=signal_data["signal"].trigger,
                 direction=signal_data["signal"].direction,
                 prompt_length=len(prompt))

        result = self.call_claude(prompt)
        if not result:
            return None

        action = result.get("action", "NO_TRADE")
        if action == "NO_TRADE":
            log.info("IH agent: NO_TRADE",
                     reasoning=result.get("reasoning", ""))
            return None
        if action != "TRADE":
            log.warning("IH agent: invalid action", action=action)
            return None

        confidence = int(result.get("confidence", 0) or 0)
        if confidence < 60:
            log.info("IH agent: confidence too low",
                     confidence=confidence,
                     reasoning=result.get("reasoning", ""))
            return None

        log.info("IH agent confirmed signal",
                 confidence=confidence,
                 skip_indices=result.get("skip_indices", []),
                 reasoning=result.get("reasoning", ""))
        return result

    def monitor_position(
        self, position: Dict, analysis: Dict, current_premium: float
    ) -> Optional[Dict]:
        """Decide HOLD / TIGHTEN_SL / EXIT_NOW for an active position.

        Returns:
            None: HOLD (or agent failed)
            dict: {action: TIGHTEN_SL|EXIT_NOW, new_sl_premium?, reasoning}
        """
        prompt = self.build_monitor_prompt(position, analysis, current_premium)

        entry = position.get("entry_premium", 0)
        pnl = ((current_premium - entry) / entry * 100) if entry > 0 else 0
        log.info("Calling IH trade monitor",
                 trade_id=position.get("id"),
                 index=position.get("index_label"),
                 pnl=f"{pnl:+.1f}%",
                 prompt_length=len(prompt))

        result = self.call_claude(prompt)
        if not result:
            return None

        action = result.get("action", "HOLD")
        if action == "HOLD":
            log.info("IH monitor: HOLD",
                     trade_id=position.get("id"),
                     index=position.get("index_label"),
                     reasoning=result.get("reasoning", ""))
            return None
        if action not in ("TIGHTEN_SL", "EXIT_NOW"):
            log.warning("IH monitor: invalid action", action=action)
            return None

        if action == "TIGHTEN_SL":
            new_sl = result.get("new_sl_premium")
            if not isinstance(new_sl, (int, float)) or new_sl <= 0:
                log.warning("IH monitor: missing/invalid new_sl_premium")
                return None
            entry = position.get("entry_premium", 0)
            old_sl = position.get("sl_premium", 0)
            if new_sl <= old_sl:
                log.info("IH monitor: TIGHTEN_SL ignored (not tighter)",
                         old=old_sl, new=new_sl)
                return None
            if new_sl >= current_premium:
                log.warning("IH monitor: TIGHTEN_SL above current",
                            new_sl=new_sl, current=current_premium)
                return None

        log.info("IH monitor decision",
                 trade_id=position.get("id"),
                 index=position.get("index_label"),
                 action=action,
                 new_sl=result.get("new_sl_premium"),
                 reasoning=result.get("reasoning", ""))
        return result

    # ------------------------------------------------------------------
    # Batched position monitoring (all positions in a single Claude call)
    # ------------------------------------------------------------------

    def build_monitor_prompt_batch(
        self,
        positions: List[Dict],
        current_premiums: Dict[int, float],
        analysis: Dict,
    ) -> str:
        """Build a single prompt covering all active positions."""
        # Collect candle data for all 3 indices (regardless of which have positions)
        index_candles = {
            "NIFTY": analysis.get("nifty_1min_candles") or [],
            "BANKNIFTY": analysis.get("banknifty_1min_candles") or [],
            "SENSEX": analysis.get("sensex_1min_candles") or [],
        }

        # Derive "now" from the latest available candle
        all_candles = [c for cs in index_candles.values() for c in cs]
        time_str = _derive_time_str(all_candles)

        # Build per-position sections
        pos_sections = []
        for i, pos in enumerate(positions, 1):
            label = pos["index_label"]
            entry = pos["entry_premium"]
            current = current_premiums.get(pos["id"], entry)
            sl = pos["sl_premium"]
            target = pos["target_premium"]
            max_p = pos.get("max_premium_reached") or entry
            min_p = pos.get("min_premium_reached") or entry
            pnl_pct = ((current - entry) / entry) * 100 if entry > 0 else 0
            pnl_rs = (current - entry) * pos["qty"]

            pos_sections.append(
                f"### Position {i}: {label} {pos['strike']} {pos['option_type']}\n"
                f"- Direction:    {pos['direction']}\n"
                f"- Quantity:     {pos['qty']}\n"
                f"- Entry:        Rs {entry:.2f}\n"
                f"- Current:      Rs {current:.2f}\n"
                f"- SL:           Rs {sl:.2f}\n"
                f"- Target:       Rs {target:.2f}\n"
                f"- Max reached:  Rs {max_p:.2f}\n"
                f"- Min reached:  Rs {min_p:.2f}\n"
                f"- P&L:          {pnl_pct:+.1f}%  (Rs {pnl_rs:+.0f})"
            )

        # Build index candle sections (all 3 always included for cross-index context)
        candle_sections = []
        for label in ("NIFTY", "BANKNIFTY", "SENSEX"):
            candle_sections.append(_format_candles(label, index_candles[label]))

        prompt = f"""{MONITOR_SYSTEM_PROMPT}

## ACTIVE POSITIONS ({len(positions)} position{'s' if len(positions) != 1 else ''} in this signal group)
- Now: {time_str}

{chr(10).join(pos_sections)}

## INDEX PRICE ACTION
{chr(10).join(candle_sections)}

## YOUR DECISION
Evaluate each position and respond per the batched output schema.
"""
        return prompt

    def monitor_positions_batch(
        self,
        positions: List[Dict],
        current_premiums: Dict[int, float],
        analysis: Dict,
    ) -> Dict[int, Optional[Dict]]:
        """Batch-monitor all positions in a single Claude call.

        Returns: {trade_id: {action, new_sl_premium?, reasoning}} for
        actionable decisions. HOLD positions are omitted (None).
        """
        if not positions:
            return {}

        prompt = self.build_monitor_prompt_batch(positions, current_premiums, analysis)

        log.info("Calling IH trade monitor (batch)",
                 positions=len(positions),
                 indices=[p["index_label"] for p in positions],
                 prompt_length=len(prompt))

        result = self.call_claude(prompt)
        if not result:
            return {}

        # Build index→trade_id lookup for matching response to positions
        index_to_pos = {p["index_label"]: p for p in positions}

        # Handle batched response: {"decisions": [...]}
        decisions_list = result.get("decisions")
        if isinstance(decisions_list, list):
            return self._parse_batch_decisions(
                decisions_list, index_to_pos, current_premiums)

        # Fallback: single-position response ({"action": ...})
        # This happens if Claude ignores the batch schema — apply to first position
        if "action" in result and len(positions) == 1:
            return self._validate_single_decision(
                result, positions[0], current_premiums)

        log.warning("IH batch monitor: unexpected response format",
                     keys=list(result.keys()))
        return {}

    def _parse_batch_decisions(
        self,
        decisions_list: list,
        index_to_pos: Dict[str, Dict],
        current_premiums: Dict[int, float],
    ) -> Dict[int, Optional[Dict]]:
        """Parse and validate each decision from the batched response."""
        results: Dict[int, Optional[Dict]] = {}

        for dec in decisions_list:
            if not isinstance(dec, dict):
                continue
            index = dec.get("index", "")
            pos = index_to_pos.get(index)
            if not pos:
                log.warning("IH batch monitor: unknown index in response",
                            index=index)
                continue

            trade_id = pos["id"]
            action = dec.get("action", "HOLD")
            current = current_premiums.get(trade_id, 0)

            if action == "HOLD":
                log.info("IH monitor: HOLD",
                         trade_id=trade_id, index=index,
                         reasoning=dec.get("reasoning", ""))
                continue

            if action not in ("TIGHTEN_SL", "EXIT_NOW"):
                log.warning("IH batch monitor: invalid action",
                            index=index, action=action)
                continue

            if action == "TIGHTEN_SL":
                new_sl = dec.get("new_sl_premium")
                if not isinstance(new_sl, (int, float)) or new_sl <= 0:
                    log.warning("IH batch monitor: invalid new_sl_premium",
                                index=index)
                    continue
                old_sl = pos.get("sl_premium", 0)
                if new_sl <= old_sl:
                    log.info("IH batch monitor: TIGHTEN_SL not tighter",
                             index=index, old=old_sl, new=new_sl)
                    continue
                if new_sl >= current:
                    log.warning("IH batch monitor: TIGHTEN_SL above current",
                                index=index, new_sl=new_sl, current=current)
                    continue

            log.info("IH monitor decision",
                     trade_id=trade_id, index=index,
                     action=action,
                     new_sl=dec.get("new_sl_premium"),
                     reasoning=dec.get("reasoning", ""))
            results[trade_id] = dec

        return results

    def _validate_single_decision(
        self,
        result: Dict,
        pos: Dict,
        current_premiums: Dict[int, float],
    ) -> Dict[int, Optional[Dict]]:
        """Fallback: validate a single-position response format."""
        trade_id = pos["id"]
        action = result.get("action", "HOLD")
        current = current_premiums.get(trade_id, 0)

        if action == "HOLD":
            log.info("IH monitor: HOLD",
                     trade_id=trade_id, index=pos["index_label"],
                     reasoning=result.get("reasoning", ""))
            return {}

        if action == "TIGHTEN_SL":
            new_sl = result.get("new_sl_premium")
            if not isinstance(new_sl, (int, float)) or new_sl <= 0:
                return {}
            if new_sl <= pos.get("sl_premium", 0) or new_sl >= current:
                return {}

        if action in ("TIGHTEN_SL", "EXIT_NOW"):
            log.info("IH monitor decision",
                     trade_id=trade_id, index=pos["index_label"],
                     action=action,
                     new_sl=result.get("new_sl_premium"),
                     reasoning=result.get("reasoning", ""))
            return {trade_id: result}
        return {}
