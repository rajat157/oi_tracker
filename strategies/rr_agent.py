"""Rally Rider Agent -- Claude Code integration for regime-aware FNO analysis.

Calls `claude -p` (non-interactive mode) as a subprocess to evaluate
mechanical signals (MC/MOM/VWAP) with regime context and premium charts.
Claude confirms or rejects, and fine-tunes entry/SL/TGT.

Follows scalper_agent.py pattern exactly for subprocess management.
"""

import json
import os
import re
import subprocess
from datetime import datetime
from typing import Optional, Dict
from core.logger import get_logger

log = get_logger("rr_agent")

CLAUDE_TIMEOUT = 120
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

SYSTEM_PROMPT = """You are an expert NIFTY FNO (Futures & Options) scalper specializing in regime-adaptive rally trading.

## YOUR ANALYSIS FRAMEWORK
1. **VWAP**: Is premium above or below VWAP? How far? Crossovers are key signals.
2. **Support/Resistance**: Premium levels with multiple bounces. Breakouts above resistance = buy signal.
3. **Swing Structure**: Higher highs + higher lows = uptrend. Lower highs + lower lows = downtrend.
4. **Momentum**: 3+ consecutive higher closes = strong momentum for CE. Vice versa for PE.
5. **IV Context**: Expanding IV favors buying. Contracting IV = caution.
6. **Regime**: Use the regime context to adapt your risk tolerance and expectations.

## RULES
- ONLY suggest LONG (buy) trades on CE or PE -- never short/sell
- Entry premium MUST be at 0.05 tick increments (e.g. 230.05, 230.10, 230.15)
- SL MUST be at a technical level: swing low, VWAP, or support level
- Target should be next resistance, recent swing high, or measured move
- Minimum risk-reward ratio: 1:1
- If no clear setup exists, respond with NO_TRADE -- being flat is valid
- Maximum 1 trade suggestion per analysis
- Confidence 0-100: below 60 means avoid, 60-75 moderate, 75+ strong

## AVOID
- Buying at resistance without breakout confirmation
- Buying after extended rally (3+ candles up without pullback)
- Buying when IV is contracting sharply
- Fighting the regime direction (e.g. CE in a PE_ONLY regime)"""

REGIME_DESCRIPTIONS = {
    "HIGH_VOL_DOWN": "High volatility downtrend. VIX elevated, large daily ranges. Favor CE (counter-trend bounces are sharp). Tight SL, quick targets.",
    "HIGH_VOL_UP": "High volatility uptrend. VIX elevated, large daily ranges. Favor PE (counter-trend dips are sharp). Quick entry, moderate hold.",
    "LOW_VOL": "Low volatility, small ranges. Only MC signals are reliable. PE favored. Wide SL, small targets, patient holds.",
    "NORMAL": "Normal market conditions. Both CE and PE valid. MC and MOM signals. Standard SL/TGT, moderate holds.",
    "TRENDING_DOWN": "Strong downtrend. MOM and VWAP signals for PE. Wide SL, large targets possible. Extended holds.",
    "TRENDING_UP": "Strong uptrend. MC and MOM signals for CE. Wide SL, large targets possible. Extended holds.",
}


class RRAgent:
    """Calls Claude Code as an FNO expert for regime-aware rally analysis."""

    def build_prompt(
        self,
        chart_text: str,
        analysis_context: Dict,
        signal: Dict,
        regime: str,
        regime_config: Dict,
        trade_history_today: list = None,
    ) -> str:
        """Build the full analysis prompt with regime context + mechanical signal + chart."""
        spot = analysis_context.get("spot_price", 0)
        vix = analysis_context.get("vix", 0) or 0
        verdict = analysis_context.get("verdict", "N/A")
        confidence = analysis_context.get("signal_confidence", 0)
        now = datetime.now()
        time_str = now.strftime("%H:%M IST")

        trade_summary = "None today"
        if trade_history_today:
            parts = []
            for t in trade_history_today:
                pnl = t.get("profit_loss_pct", 0)
                emoji = "W" if pnl > 0 else "L"
                parts.append(f"{emoji} {t.get('option_type', '?')} {pnl:+.1f}%")
            trade_summary = " | ".join(parts)

        regime_desc = REGIME_DESCRIPTIONS.get(regime, "Unknown regime")
        direction_filter = regime_config.get("direction", "BOTH")
        sl_pts = regime_config.get("sl_pts", 40)
        tgt_pts = regime_config.get("tgt_pts", 20)
        max_hold = regime_config.get("max_hold", 35)

        signal_type = signal.get("signal_type", "?")
        signal_dir = signal.get("direction", "?")
        signal_data = signal.get("signal_data", {})

        signal_details = f"Type: {signal_type}, Direction: {signal_dir}"
        for k, v in signal_data.items():
            if isinstance(v, float):
                signal_details += f", {k}: {v:.2f}"
            else:
                signal_details += f", {k}: {v}"

        prompt = f"""{SYSTEM_PROMPT}

## REGIME CONTEXT
- Current Regime: **{regime}**
- Description: {regime_desc}
- Direction Filter: {direction_filter}
- Backtested Params: SL={sl_pts} pts, TGT={tgt_pts} pts, Max Hold={max_hold} min

## MECHANICAL SIGNAL (pre-detected)
{signal_details}

## CURRENT MARKET CONTEXT
- Spot Price: {spot:.2f}
- VIX: {vix:.1f}
- OI Verdict: {verdict} ({confidence:.0f}% confidence)
- Time: {time_str}
- Today's RR Trades: {trade_summary}

{chart_text}

## YOUR TASK
A mechanical {signal_type} signal has been detected. Evaluate if this is a good entry using the premium chart above and regime context.

- If the setup looks good, suggest entry/SL/TGT premiums (at 0.05 ticks)
- The regime suggests SL~{sl_pts}pts / TGT~{tgt_pts}pts but use your technical judgment
- If the chart doesn't support the signal, say NO_TRADE

Respond with ONLY valid JSON (no markdown, no explanation outside the JSON):
{{"action": "BUY_CE" or "BUY_PE" or "NO_TRADE", "strike": <int>, "option_type": "CE" or "PE", "entry_premium": <float>, "sl_premium": <float>, "target_premium": <float>, "confidence": <int 0-100>, "reasoning": "<brief 1-2 sentence explanation>"}}"""
        return prompt

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
            if isinstance(data, dict):
                return data
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
        """Validate a trade signal for sanity."""
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

        if sl >= entry:
            log.warning("SL >= entry (invalid for buy)", sl=sl, entry=entry)
            return False

        if target <= entry:
            log.warning("Target <= entry (invalid for buy)",
                        target=target, entry=entry)
            return False

        # Risk-reward check (minimum 0.8:1)
        risk = entry - sl
        reward = target - entry
        if risk > 0 and reward / risk < 0.8:
            log.warning("Risk-reward too low", rr=f"{reward/risk:.2f}")
            return False

        confidence = signal.get("confidence", 0)
        if not isinstance(confidence, (int, float)):
            signal["confidence"] = 50

        return True

    def get_signal(
        self,
        chart_text: str,
        analysis_context: Dict,
        signal: Dict,
        regime: str,
        regime_config: Dict,
        trade_history_today: list = None,
    ) -> Optional[Dict]:
        """Full pipeline: build prompt -> call Claude -> parse & validate."""
        prompt = self.build_prompt(
            chart_text, analysis_context, signal,
            regime, regime_config, trade_history_today,
        )

        log.info("Calling Claude agent for RR analysis",
                 regime=regime, signal_type=signal.get("signal_type"),
                 prompt_length=len(prompt))

        result = self.call_claude(prompt)
        if not result:
            return None

        action = result.get("action", "NO_TRADE")
        if action == "NO_TRADE":
            log.info("Claude suggests NO_TRADE",
                     reasoning=result.get("reasoning", ""))
            return None

        if not self._validate_signal(result):
            log.warning("Claude signal failed validation", signal=result)
            return None

        log.info("Claude RR signal received",
                 action=action,
                 strike=result.get("strike"),
                 entry=result.get("entry_premium"),
                 sl=result.get("sl_premium"),
                 target=result.get("target_premium"),
                 confidence=result.get("confidence"))

        return result
