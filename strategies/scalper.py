"""Scalper strategy — Claude-powered FNO expert, multi-trade/day.

Fully self-contained: all logic absorbed from scalper_tracker.py.
External deps (ScalperEngine, ScalperAgent) stay as instance attributes.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Dict, Optional

from config import ScalperConfig
from core.base_tracker import BaseTracker
from core.events import EventType
from db.schema import SCALP_TRADES_DDL
from core.logger import get_logger

log = get_logger("scalper_tracker")

_cfg = ScalperConfig()


class ScalperStrategy(BaseTracker):
    tracker_type = "scalper"
    table_name = "scalp_trades"
    time_start = _cfg.TIME_START
    time_end = _cfg.TIME_END
    force_close_time = _cfg.FORCE_CLOSE_TIME
    max_trades_per_day = _cfg.MAX_TRADES_PER_DAY
    is_selling = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.trade_repo:
            self.trade_repo.init_table(SCALP_TRADES_DDL)
        self._engine = None
        self._agent = None

    @property
    def engine(self):
        if self._engine is None:
            from strategies.scalper_engine import ScalperEngine
            self._engine = ScalperEngine()
        return self._engine

    @property
    def agent(self):
        if self._agent is None:
            from strategies.scalper_agent import ScalperAgent
            self._agent = ScalperAgent()
        return self._agent

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    def should_create(self, analysis: dict, **kwargs) -> bool:
        now = datetime.now()

        if not self.is_in_time_window(now):
            return False

        # Need at least 15 min of market data
        if now.time() < time(9, 45):
            return False

        if self.trade_repo is None:
            return False

        if self.trade_repo.get_active(self.table_name):
            return False

        todays = self.trade_repo.get_todays_trades(self.table_name)
        if len(todays) >= self.max_trades_per_day:
            return False

        # Cooldown since last trade
        if todays:
            last = todays[-1]
            resolved_at = last.get("resolved_at")
            if resolved_at:
                last_resolved = (datetime.fromisoformat(resolved_at)
                                 if isinstance(resolved_at, str) else resolved_at)
                elapsed = (now - last_resolved).total_seconds()
                if elapsed < _cfg.COOLDOWN_MINUTES * 60:
                    log.debug("Scalp cooldown active",
                              elapsed=f"{elapsed:.0f}s",
                              required=f"{_cfg.COOLDOWN_MINUTES * 60}s")
                    return False
            else:
                return False  # last trade still active

        spot = analysis.get("spot_price", 0)
        if spot <= 0:
            return False

        return True

    def get_agent_signal(self, analysis: dict, strikes_data: dict) -> Optional[Dict]:
        """Build chart -> run pre-filter -> call Claude -> return signal."""
        spot = analysis.get("spot_price", 0)
        strikes = self.engine.get_scalp_strikes(spot)
        ce_strike = strikes["ce_strike"]
        pe_strike = strikes["pe_strike"]

        chart = self.engine.build_premium_chart(spot, ce_strike=ce_strike, pe_strike=pe_strike)
        if not chart:
            log.info("No chart data available for scalper")
            return None

        min_candles = 5
        ce_ok = len(chart["ce_candles"]) >= min_candles
        pe_ok = len(chart["pe_candles"]) >= min_candles
        if not ce_ok and not pe_ok:
            log.debug("Not enough candles for scalper analysis",
                      ce=len(chart["ce_candles"]), pe=len(chart["pe_candles"]))
            return None

        ce_analysis = self.engine.analyze_side(chart["ce_candles"]) if ce_ok else {"has_setup": False}
        pe_analysis = self.engine.analyze_side(chart["pe_candles"]) if pe_ok else {"has_setup": False}

        chart_text = self.engine.format_chart_for_prompt(chart)

        todays = self.trade_repo.get_todays_trades(self.table_name)
        resolved = [t for t in todays if t.get("resolved_at")]

        signal = self.agent.get_signal(chart_text, analysis, resolved)
        if not signal:
            return None

        side = signal.get("option_type", "CE")
        side_analysis = ce_analysis if side == "CE" else pe_analysis
        signal["_vwap"] = side_analysis.get("current_vwap", 0)
        signal["_candles"] = chart[f"{'ce' if side == 'CE' else 'pe'}_candles"]

        return signal

    def create_trade(self, signal: Any, analysis: dict, strikes_data: dict,
                     **kwargs) -> Optional[int]:
        if not isinstance(signal, dict):
            return None

        strike = signal.get("strike", 0)
        entry = signal.get("entry_premium", 0)
        sl = signal.get("sl_premium", 0)
        target = signal.get("target_premium", 0)
        confidence = signal.get("confidence", 0)
        reasoning = signal.get("reasoning", "")
        option_type = signal.get("option_type", "")

        if not strike or not entry:
            log.warning("Invalid signal for trade creation", signal=signal)
            return None

        if confidence < _cfg.MIN_AGENT_CONFIDENCE:
            log.info("Scalp skipped: low confidence",
                     confidence=confidence, min=_cfg.MIN_AGENT_CONFIDENCE)
            return None

        if entry < _cfg.MIN_PREMIUM:
            log.info("Scalp skipped: premium too low", premium=entry)
            return None
        if entry > _cfg.MAX_PREMIUM:
            log.info("Scalp skipped: premium too high", premium=entry)
            return None

        # Cap SL risk
        sl_pct = (entry - sl) / entry * 100
        if sl_pct > _cfg.MAX_SL_PCT:
            sl = round(entry * (1 - _cfg.MAX_SL_PCT / 100), 2)
            log.info("SL capped", original_sl_pct=f"{sl_pct:.1f}%",
                     capped_to=f"{_cfg.MAX_SL_PCT}%", new_sl=sl)

        spot = analysis.get("spot_price", 0)
        verdict = analysis.get("verdict", "")
        vix = analysis.get("vix", 0) or 0

        candles = signal.get("_candles", [])
        iv = candles[-1]["iv"] if candles else 0
        vwap = signal.get("_vwap", 0)

        direction = f"BUY_{option_type}"
        todays = self.trade_repo.get_todays_trades(self.table_name)
        trade_number = len(todays) + 1

        now = datetime.now()
        trade_id = self.trade_repo.insert_trade(
            self.table_name,
            created_at=now,
            direction=direction,
            strike=strike,
            option_type=option_type,
            entry_premium=entry,
            sl_premium=sl,
            target_premium=target,
            spot_at_creation=spot,
            verdict_at_creation=verdict,
            signal_confidence=confidence,
            vix_at_creation=vix,
            iv_at_creation=iv,
            vwap_at_creation=vwap,
            agent_reasoning=reasoning,
            status="ACTIVE",
            max_premium_reached=entry,
            min_premium_reached=entry,
            trade_number=trade_number,
        )

        log.info("Created scalp trade",
                 trade_id=trade_id, direction=direction,
                 strike=f"{strike} {option_type}",
                 entry=entry, sl=sl, target=target,
                 confidence=confidence, trade_num=trade_number)

        self._publish(EventType.TRADE_CREATED, {
            "trade_id": trade_id, "direction": direction, "strike": strike,
            "option_type": option_type, "entry": entry, "sl": sl, "target": target,
            "alert_message": self._format_entry_alert(
                direction, strike, option_type, entry, sl, target,
                spot, verdict, confidence, vix, reasoning, trade_number),
        })
        return trade_id

    def check_and_update(self, strikes_data: dict, **kwargs) -> Optional[Dict]:
        trade = self.get_active()
        if not trade:
            return None

        strike = trade["strike"]
        option_type = trade["option_type"]
        current = (strikes_data.get(strike, {})
                   .get("ce_ltp" if option_type == "CE" else "pe_ltp", 0))
        if current <= 0:
            return None

        now = datetime.now()
        entry = trade["entry_premium"]
        max_p = max(trade.get("max_premium_reached") or entry, current)
        min_p = min(trade.get("min_premium_reached") or entry, current)

        self.trade_repo.update_trade(
            self.table_name, trade["id"],
            last_checked_at=now, last_premium=current,
            max_premium_reached=max_p, min_premium_reached=min_p,
        )

        def _resolve(status, reason):
            pnl = ((current - entry) / entry) * 100
            self.trade_repo.update_trade(
                self.table_name, trade["id"],
                status=status, resolved_at=now,
                exit_premium=current, exit_reason=reason,
                profit_loss_pct=pnl,
            )
            log.info(f"Scalp {status} ({reason})", pnl=f"{pnl:.2f}%",
                     entry=entry, exit=current)
            self._publish(EventType.TRADE_EXITED, {
                "trade_id": trade["id"], "action": status, "pnl": pnl,
                "reason": reason,
                "alert_message": self._format_exit_alert(trade, current, reason, pnl),
            })
            return {"action": status, "pnl": pnl, "reason": reason}

        if current <= trade["sl_premium"]:
            return _resolve("LOST", "SL")
        if current >= trade["target_premium"]:
            return _resolve("WON", "TARGET")
        if self.is_past_force_close(now):
            return _resolve("WON" if ((current - entry) / entry * 100) > 0 else "LOST", "EOD")
        return None

    def get_active(self) -> Optional[Dict]:
        if self.trade_repo is None:
            return None
        return self.trade_repo.get_active(self.table_name)

    def get_stats(self, lookback_days: int = 30) -> Dict:
        if self.trade_repo is None:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "avg_win": 0, "avg_loss": 0, "total_pnl": 0}
        return self.trade_repo.get_stats(self.table_name, lookback_days)

    # ------------------------------------------------------------------
    # Alert formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_entry_alert(direction, strike, option_type, entry, sl, target,
                            spot, verdict, confidence, vix, reasoning,
                            trade_number) -> str:
        side_emoji = "\U0001f7e2" if option_type == "CE" else "\U0001f534"
        risk = entry - sl
        reward = target - entry
        rr = f"1:{reward/risk:.1f}" if risk > 0 else "N/A"
        sl_pct = (entry - sl) / entry * 100
        target_pct = (target - entry) / entry * 100
        return (
            f"<b>{side_emoji} SCALPER: {direction}</b> (#{trade_number})\n\n"
            f"<b>Strike:</b> <code>{strike} {option_type}</code>\n"
            f"<b>Spot:</b> <code>{spot:.2f}</code>\n"
            f"<b>Entry:</b> <code>Rs {entry:.2f}</code>\n"
            f"<b>SL:</b> <code>Rs {sl:.2f}</code> (-{sl_pct:.1f}%)\n"
            f"<b>Target:</b> <code>Rs {target:.2f}</code> (+{target_pct:.1f}%)\n"
            f"<b>RR:</b> <code>{rr}</code>\n"
            f"<b>Confidence:</b> {confidence}%\n\n"
            f"<b>Verdict:</b> {verdict}\n"
            f"<b>VIX:</b> {vix:.1f}\n\n"
            f"<b>Agent Reasoning:</b>\n<i>{reasoning}</i>\n\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )

    @staticmethod
    def _format_exit_alert(trade, exit_premium, reason, pnl) -> str:
        result_emoji = "\u2705" if pnl > 0 else "\u274c"
        reason_text = {"TARGET": "Target Hit", "SL": "Stop Loss",
                       "EOD": "End of Day"}.get(reason, reason)
        created = (datetime.fromisoformat(trade["created_at"])
                   if isinstance(trade["created_at"], str) else trade["created_at"])
        duration = datetime.now() - created
        duration_str = f"{int(duration.total_seconds() / 60)}m"
        return (
            f"<b>{result_emoji} SCALP {'WON' if pnl > 0 else 'LOST'}</b> (#{trade.get('trade_number', '?')})\n\n"
            f"<b>Strike:</b> <code>{trade['strike']} {trade['option_type']}</code>\n"
            f"<b>Entry:</b> <code>Rs {trade['entry_premium']:.2f}</code>\n"
            f"<b>Exit:</b> <code>Rs {exit_premium:.2f}</code>\n"
            f"<b>P&L:</b> <code>{pnl:+.2f}%</code>\n"
            f"<b>Duration:</b> {duration_str}\n"
            f"<b>Reason:</b> {reason_text}\n\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )
