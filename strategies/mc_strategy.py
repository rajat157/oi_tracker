"""MC (Momentum Continuation) strategy — mechanical rally catcher.

Runs independently alongside the Scalper Agent. Detects intraday NIFTY
rallies that have pulled back and are resuming, enters ITM options
mechanically (no Claude agent). Paper trading only.

Max 1 trade/day, 10:00-14:00 window, 2-stage trailing stop.
"""

from __future__ import annotations

import json
from datetime import datetime, time
from typing import Any, Dict, Optional

from config import MCConfig
from core.base_tracker import BaseTracker
from core.events import EventType
from core.logger import get_logger
from db.schema import MC_TRADES_DDL

log = get_logger("mc_strategy")

_cfg = MCConfig()


class MCStrategy(BaseTracker):
    tracker_type = "mc"
    table_name = "mc_trades"
    time_start = _cfg.TIME_START
    time_end = _cfg.TIME_END
    force_close_time = _cfg.FORCE_CLOSE_TIME
    max_trades_per_day = _cfg.MAX_TRADES_PER_DAY
    is_selling = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.trade_repo:
            self.trade_repo.init_table(MC_TRADES_DDL)
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            from strategies.mc_engine import MCEngine
            self._engine = MCEngine()
        return self._engine

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    def should_create(self, analysis: dict, **kwargs) -> bool:
        now = datetime.now()

        if not self.is_in_time_window(now):
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
                    return False
            else:
                return False  # last trade still active

        spot = analysis.get("spot_price", 0)
        if spot <= 0:
            return False

        return True

    def evaluate_signal(self, analysis: dict, strikes_data: dict) -> Optional[Dict]:
        """Detect MC signal mechanically. No Claude agent needed."""
        return self.engine.detect_mc_signal(analysis, strikes_data, _cfg)

    def create_trade(self, signal: Any, analysis: dict, strikes_data: dict,
                     **kwargs) -> Optional[int]:
        if not isinstance(signal, dict):
            return None

        strike = signal.get("strike", 0)
        entry = signal.get("entry_premium", 0)
        sl = signal.get("sl_premium", 0)
        target = signal.get("target_premium", 0)
        option_type = signal.get("option_type", "")
        direction = signal.get("direction", "")
        signal_data = signal.get("signal_data", {})

        if not strike or not entry:
            log.warning("MC: invalid signal", signal=signal)
            return None

        if entry < _cfg.MIN_PREMIUM or entry > _cfg.MAX_PREMIUM:
            log.info("MC: premium out of range", premium=entry)
            return None

        spot = analysis.get("spot_price", 0)
        verdict = analysis.get("verdict", "")
        vix = analysis.get("vix", 0) or 0

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
            signal_type="MC",
            signal_data_json=json.dumps(signal_data),
            vix_at_creation=vix,
            status="ACTIVE",
            max_premium_reached=entry,
            min_premium_reached=entry,
            trail_stage=0,
            trade_number=trade_number,
        )

        log.info("MC trade created",
                 trade_id=trade_id, direction=direction,
                 strike=f"{strike} {option_type}",
                 entry=entry, sl=sl, target=target,
                 rally_pts=signal_data.get("rally_pts"),
                 pullback=signal_data.get("pullback_pct"))

        self._publish(EventType.TRADE_CREATED, {
            "trade_id": trade_id, "direction": direction, "strike": strike,
            "option_type": option_type, "entry": entry, "sl": sl, "target": target,
            "alert_message": self._format_entry_alert(
                direction, strike, option_type, entry, sl, target,
                spot, verdict, vix, signal_data, trade_number),
        })
        return trade_id

    def check_and_update(self, strikes_data: dict, **kwargs) -> Optional[Dict]:
        trade = self.get_active()
        if not trade:
            return None

        strike = trade["strike"]
        option_type = trade["option_type"]
        key = "ce_ltp" if option_type == "CE" else "pe_ltp"
        current = strikes_data.get(strike, {}).get(key, 0)
        if current <= 0:
            return None

        now = datetime.now()
        entry = trade["entry_premium"]
        max_p = max(trade.get("max_premium_reached") or entry, current)
        min_p = min(trade.get("min_premium_reached") or entry, current)
        trail_stage = trade.get("trail_stage", 0) or 0
        sl = trade["sl_premium"]

        # Trailing stop logic
        pnl_pct = ((current - entry) / entry) * 100
        new_trail_stage = trail_stage

        if pnl_pct >= _cfg.TRAIL_2_TRIGGER and trail_stage < 2:
            new_sl = round(entry * (1 + _cfg.TRAIL_2_LOCK / 100), 2)
            if new_sl > sl:
                sl = new_sl
                new_trail_stage = 2
                log.info("MC trail stage 2",
                         trade_id=trade["id"], sl=sl, pnl=f"{pnl_pct:.1f}%")
        elif pnl_pct >= _cfg.TRAIL_1_TRIGGER and trail_stage < 1:
            new_sl = round(entry * (1 + _cfg.TRAIL_1_LOCK / 100), 2)
            if new_sl > sl:
                sl = new_sl
                new_trail_stage = 1
                log.info("MC trail stage 1",
                         trade_id=trade["id"], sl=sl, pnl=f"{pnl_pct:.1f}%")

        updates = dict(
            last_checked_at=now, last_premium=current,
            max_premium_reached=max_p, min_premium_reached=min_p,
        )
        if new_trail_stage != trail_stage:
            updates["trail_stage"] = new_trail_stage
            updates["sl_premium"] = sl

        self.trade_repo.update_trade(self.table_name, trade["id"], **updates)

        def _resolve(status, reason):
            final_pnl = ((current - entry) / entry) * 100
            self.trade_repo.update_trade(
                self.table_name, trade["id"],
                status=status, resolved_at=now,
                exit_premium=current, exit_reason=reason,
                profit_loss_pct=final_pnl,
            )
            log.info(f"MC {status} ({reason})", pnl=f"{final_pnl:.2f}%",
                     entry=entry, exit=current, trade_id=trade["id"])
            self._publish(EventType.TRADE_EXITED, {
                "trade_id": trade["id"], "action": status, "pnl": final_pnl,
                "reason": reason,
                "alert_message": self._format_exit_alert(trade, current, reason, final_pnl),
            })
            return {"action": status, "pnl": final_pnl, "reason": reason}

        # SL hit
        if current <= sl:
            return _resolve("LOST", "TRAIL_SL" if trail_stage > 0 else "SL")

        # Target hit
        if current >= trade["target_premium"]:
            return _resolve("WON", "TARGET")

        # Time-based exits
        created = (datetime.fromisoformat(trade["created_at"])
                   if isinstance(trade["created_at"], str) else trade["created_at"])
        elapsed_min = (now - created).total_seconds() / 60

        if elapsed_min >= _cfg.MAX_DURATION_MIN:
            status = "WON" if pnl_pct > 0 else "LOST"
            return _resolve(status, "MAX_TIME")

        if elapsed_min >= _cfg.TIME_EXIT_MIN and abs(pnl_pct) < _cfg.TIME_EXIT_DEAD_PCT:
            status = "WON" if pnl_pct > 0 else "LOST"
            return _resolve(status, "TIME_FLAT")

        # EOD force close
        if self.is_past_force_close(now):
            status = "WON" if pnl_pct > 0 else "LOST"
            return _resolve(status, "EOD")

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
                            spot, verdict, vix, signal_data, trade_number) -> str:
        side_emoji = "\U0001f7e2" if option_type == "CE" else "\U0001f534"
        risk = entry - sl
        reward = target - entry
        rr = f"1:{reward / risk:.1f}" if risk > 0 else "N/A"
        sl_pct = (entry - sl) / entry * 100
        target_pct = (target - entry) / entry * 100
        rally_pts = signal_data.get("rally_pts", 0)
        pullback_pct = signal_data.get("pullback_pct", 0)
        weekly = signal_data.get("weekly_trend", "?")
        return (
            f"<b>{side_emoji} MC RALLY: {direction}</b>\n\n"
            f"<b>Strike:</b> <code>{strike} {option_type}</code>\n"
            f"<b>Spot:</b> <code>{spot:.2f}</code>\n"
            f"<b>Entry:</b> <code>Rs {entry:.2f}</code>\n"
            f"<b>SL:</b> <code>Rs {sl:.2f}</code> (-{sl_pct:.1f}%)\n"
            f"<b>Target:</b> <code>Rs {target:.2f}</code> (+{target_pct:.1f}%)\n"
            f"<b>RR:</b> <code>{rr}</code>\n\n"
            f"<b>Rally:</b> {rally_pts:.0f} pts | "
            f"<b>Pullback:</b> {pullback_pct:.0%}\n"
            f"<b>Weekly Trend:</b> {weekly}\n"
            f"<b>Verdict:</b> {verdict}\n"
            f"<b>VIX:</b> {vix:.1f}\n\n"
            f"<i>Mechanical signal (no agent) | "
            f"{datetime.now().strftime('%H:%M:%S')}</i>"
        )

    @staticmethod
    def _format_exit_alert(trade, exit_premium, reason, pnl) -> str:
        result_emoji = "\u2705" if pnl > 0 else "\u274c"
        reason_map = {"TARGET": "Target Hit", "SL": "Stop Loss",
                      "TRAIL_SL": "Trailing Stop", "EOD": "End of Day",
                      "TIME_FLAT": "Time Exit (flat)", "MAX_TIME": "Max Duration"}
        reason_text = reason_map.get(reason, reason)
        created = (datetime.fromisoformat(trade["created_at"])
                   if isinstance(trade["created_at"], str) else trade["created_at"])
        duration = datetime.now() - created
        duration_str = f"{int(duration.total_seconds() / 60)}m"
        return (
            f"<b>{result_emoji} MC {'WON' if pnl > 0 else 'LOST'}</b>\n\n"
            f"<b>Strike:</b> <code>{trade['strike']} {trade['option_type']}</code>\n"
            f"<b>Entry:</b> <code>Rs {trade['entry_premium']:.2f}</code>\n"
            f"<b>Exit:</b> <code>Rs {exit_premium:.2f}</code>\n"
            f"<b>P&L:</b> <code>{pnl:+.2f}%</code>\n"
            f"<b>Duration:</b> {duration_str}\n"
            f"<b>Reason:</b> {reason_text}\n\n"
            f"<i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )
