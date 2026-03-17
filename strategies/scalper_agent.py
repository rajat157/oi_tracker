"""
Scalper Agent -- Claude Code integration for FNO expert analysis.

Calls `claude -p` (non-interactive mode) as a subprocess to analyze
premium charts and suggest quick scalping trades.

The agent receives the full day's premium chart context, technical
indicators (VWAP, S/R, swings), and market context (OI verdict, VIX),
and responds with structured JSON trade signals.
"""

import json
import os
import re
import subprocess
from datetime import datetime
from typing import Optional, Dict
from core.logger import get_logger

log = get_logger("scalper_agent")

CLAUDE_TIMEOUT = 120  # seconds
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

SYSTEM_PROMPT = """You are an expert NIFTY FNO (Futures & Options) scalper. You analyze 3-minute option premium charts and identify quick scalping LONG (buy) opportunities.

## YOUR ANALYSIS FRAMEWORK
1. **VWAP**: Is premium above or below VWAP? How far? Crossovers are key signals.
2. **Support/Resistance**: Premium levels with multiple bounces. Breakouts above resistance = buy signal.
3. **Swing Structure**: Higher highs + higher lows = uptrend. Lower highs + lower lows = downtrend.
4. **Breakout/Breakdown**: Premium breaking key S/R levels with volume confirmation.
5. **IV Context**: Expanding IV favors buying. Contracting IV = caution.
6. **Volume**: Higher volume on breakout candles adds conviction.
7. **Momentum**: 3+ consecutive higher closes (CHC) = strong momentum.

## RULES
- ONLY suggest LONG (buy) trades on CE or PE -- never short/sell
- NEVER buy into a clear premium downtrend (lower lows, below VWAP, falling momentum)
- SL MUST be at a technical level: swing low, VWAP, or support level
- Target should be next resistance, recent swing high, or a measured move
- Minimum risk-reward ratio: 1:1
- If no clear setup exists, respond with NO_TRADE -- being flat is a valid position
- Maximum 1 trade suggestion per analysis
- Confidence 0-100: below 60 means avoid, 60-75 moderate, 75+ strong

## PREFERRED SETUPS (highest to lowest priority)
1. VWAP reclaim: Premium dips below VWAP, consolidates, then crosses back above with volume
2. Support bounce: Premium touches support (2+ touches) and shows reversal candle
3. Resistance breakout: Premium breaks above a level tested 2+ times, with momentum
4. Momentum continuation: Premium in uptrend (above VWAP), pullback to VWAP, resume higher

## AVOID
- Buying at resistance without breakout confirmation
- Buying after extended rally (3+ candles up without pullback)
- Buying when IV is contracting sharply (premium decay will hurt)
- Buying in the last 45 minutes of trading (theta decay accelerates)"""


class ScalperAgent:
    """Calls Claude Code as an FNO expert for premium chart analysis."""

    def build_prompt(self, chart_text: str, analysis_context: Dict,
                     trade_history_today: list = None) -> str:
        """
        Build the full analysis prompt with chart data and market context.

        Args:
            chart_text: Formatted premium chart from ScalperEngine
            analysis_context: Current OI analysis dict
            trade_history_today: List of today's completed scalp trades
        """
        spot = analysis_context.get("spot_price", 0)
        vix = analysis_context.get("vix", 0) or 0
        verdict = analysis_context.get("verdict", "N/A")
        confidence = analysis_context.get("signal_confidence", 0)
        now = datetime.now()
        time_str = now.strftime("%H:%M IST")

        # Trade history summary
        trade_summary = "None today"
        if trade_history_today:
            parts = []
            for t in trade_history_today:
                pnl = t.get("profit_loss_pct", 0)
                emoji = "W" if pnl > 0 else "L"
                parts.append(f"{emoji} {t.get('option_type', '?')} {pnl:+.1f}%")
            trade_summary = " | ".join(parts)

        prompt = f"""{SYSTEM_PROMPT}

## CURRENT MARKET CONTEXT
- Spot Price: {spot:.2f}
- VIX: {vix:.1f}
- OI Verdict: {verdict} ({confidence:.0f}% confidence)
- Time: {time_str}
- Today's Scalp Trades: {trade_summary}

{chart_text}

## YOUR TASK
Analyze both CE and PE premium charts above. If you see a clear scalping setup, provide your recommendation. If no clear setup exists, say NO_TRADE.

Respond with ONLY valid JSON (no markdown, no explanation outside the JSON):
{{"action": "BUY_CE" or "BUY_PE" or "NO_TRADE", "strike": <int>, "option_type": "CE" or "PE", "entry_premium": <float>, "sl_premium": <float>, "target_premium": <float>, "confidence": <int 0-100>, "reasoning": "<brief 1-2 sentence explanation>"}}"""
        return prompt

    def call_claude(self, prompt: str) -> Optional[Dict]:
        """
        Call claude -p subprocess and parse the JSON response.

        Returns parsed signal dict or None on failure.
        """
        try:
            # Strip CLAUDECODE env var to prevent nested session error
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
                log.error("Claude subprocess failed",
                          returncode=result.returncode,
                          stderr=result.stderr[:500] if result.stderr else "")
                return None

            raw = result.stdout.strip()
            if not raw:
                log.warning("Claude returned empty response")
                return None

            log.info("Claude response received", length=len(raw))
            return self._parse_response(raw)

        except subprocess.TimeoutExpired:
            log.error("Claude subprocess timed out", timeout=CLAUDE_TIMEOUT)
            return None
        except FileNotFoundError:
            log.error("Claude CLI not found -- is it installed and on PATH?")
            return None
        except Exception as e:
            log.error("Claude subprocess error", error=str(e))
            return None

    def _parse_response(self, raw: str) -> Optional[Dict]:
        """Extract and parse JSON from Claude's response text."""
        # Try direct JSON parse first
        try:
            data = json.loads(raw)
            if self._validate_signal(data):
                return data
            return data  # Return even if NO_TRADE
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from markdown code block
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

        # Try finding JSON object in the text
        json_match = re.search(r'\{[^{}]*"action"[^{}]*\}', raw, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

        log.warning("Failed to parse Claude response as JSON",
                    raw_preview=raw[:200])
        return None

    def _validate_signal(self, signal: Dict) -> bool:
        """
        Validate a trade signal for sanity.
        Returns True if signal is a valid trade (not NO_TRADE) and passes checks.
        """
        action = signal.get("action", "")
        if action == "NO_TRADE":
            return False

        if action not in ("BUY_CE", "BUY_PE"):
            log.warning("Invalid action in signal", action=action)
            return False

        entry = signal.get("entry_premium", 0)
        sl = signal.get("sl_premium", 0)
        target = signal.get("target_premium", 0)

        if not all(isinstance(v, (int, float)) for v in [entry, sl, target]):
            log.warning("Non-numeric premium values in signal")
            return False

        if entry <= 0 or sl <= 0 or target <= 0:
            log.warning("Zero or negative premiums in signal",
                        entry=entry, sl=sl, target=target)
            return False

        # SL must be below entry (buying)
        if sl >= entry:
            log.warning("SL >= entry (invalid for buy)",
                        sl=sl, entry=entry)
            return False

        # Target must be above entry
        if target <= entry:
            log.warning("Target <= entry (invalid for buy)",
                        target=target, entry=entry)
            return False

        # Risk-reward check (minimum 0.8:1)
        risk = entry - sl
        reward = target - entry
        if risk > 0 and reward / risk < 0.8:
            log.warning("Risk-reward too low",
                        rr=f"{reward/risk:.2f}")
            return False

        # Confidence check
        confidence = signal.get("confidence", 0)
        if not isinstance(confidence, (int, float)):
            signal["confidence"] = 50  # Default if missing

        return True

    def get_signal(self, chart_text: str, analysis_context: Dict,
                   trade_history_today: list = None) -> Optional[Dict]:
        """
        Full pipeline: build prompt -> call Claude -> parse & validate.
        Returns signal dict or None.
        """
        prompt = self.build_prompt(chart_text, analysis_context, trade_history_today)

        log.info("Calling Claude agent for scalp analysis",
                 prompt_length=len(prompt))

        signal = self.call_claude(prompt)
        if not signal:
            return None

        action = signal.get("action", "NO_TRADE")
        if action == "NO_TRADE":
            log.info("Claude suggests NO_TRADE",
                     reasoning=signal.get("reasoning", ""))
            return None

        if not self._validate_signal(signal):
            log.warning("Claude signal failed validation", signal=signal)
            return None

        log.info("Claude signal received",
                 action=action,
                 strike=signal.get("strike"),
                 entry=signal.get("entry_premium"),
                 sl=signal.get("sl_premium"),
                 target=signal.get("target_premium"),
                 confidence=signal.get("confidence"))

        return signal

    def monitor_active_trade(
        self,
        chart_text: str,
        trade_context: Dict,
        analysis_context: Dict,
    ) -> Optional[Dict]:
        """Evaluate an active trade: HOLD, TIGHTEN_SL, or EXIT_NOW.

        Returns dict with action/new_sl_premium/reasoning, or None (= HOLD).
        """
        from strategies.trade_monitor import (
            build_monitor_prompt, validate_monitor_response,
        )

        prompt = build_monitor_prompt(chart_text, trade_context, analysis_context)

        log.info("Calling Claude for trade monitoring",
                 trade_id=trade_context.get("trade_id"),
                 pnl=f"{trade_context.get('pnl_pct', 0):+.1f}%",
                 prompt_length=len(prompt))

        result = self.call_claude(prompt)
        if not result:
            return None

        action = result.get("action", "HOLD")
        if action == "HOLD":
            log.info("Claude monitor: HOLD",
                     reasoning=result.get("reasoning", ""))
            return None

        if not validate_monitor_response(result, trade_context):
            log.warning("Claude monitor response invalid", result=result)
            return None

        log.info("Claude monitor action",
                 action=action,
                 new_sl=result.get("new_sl_premium"),
                 reasoning=result.get("reasoning", ""))

        return result
