"""Momentum strategy — trend-following 1:2 RR buying on high-conviction days.

Fully self-contained: no legacy momentum_tracker.py imports.
"""

from __future__ import annotations

import json
from datetime import datetime, time
from typing import Any, Dict, Optional

from config import MomentumConfig, MarketConfig
from core.base_tracker import BaseTracker
from core.events import EventType
from db.schema import MOMENTUM_TRADES_DDL
from core.logger import get_logger

log = get_logger("momentum_tracker")

_cfg = MomentumConfig()
_mkt = MarketConfig()


class MomentumStrategy(BaseTracker):
    tracker_type = "momentum"
    table_name = "momentum_trades"
    time_start = _cfg.TIME_START
    time_end = _cfg.TIME_END
    force_close_time = _cfg.FORCE_CLOSE_TIME
    max_trades_per_day = 1
    is_selling = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.trade_repo:
            self.trade_repo.init_table(MOMENTUM_TRADES_DDL)

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    def should_create(self, analysis: dict, **kwargs) -> bool:
        return self._evaluate(analysis) is not None

    def evaluate(self, analysis: dict) -> Optional[str]:
        """Return direction ('BUY_PUT'/'BUY_CALL') or None."""
        return self._evaluate(analysis)

    def create_trade(self, signal: Any, analysis: dict, strikes_data: dict,
                     **kwargs) -> Optional[int]:
        direction = signal if isinstance(signal, str) else signal.direction
        return self._create(direction, analysis, strikes_data)

    def check_and_update(self, strikes_data: dict, **kwargs) -> Optional[Dict]:
        return self._check(strikes_data)

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
    # Core logic
    # ------------------------------------------------------------------

    def _evaluate(self, analysis: dict) -> Optional[str]:
        if self.trade_repo:
            if self.trade_repo.get_todays_trades(self.table_name):
                return None
            if self.trade_repo.get_active(self.table_name):
                return None
        elif self.already_traded_today():
            return None

        if not self.is_in_time_window():
            return None

        confidence = analysis.get("signal_confidence", 0) or 0
        if confidence < _cfg.MIN_CONFIDENCE:
            return None

        confirmation = self._get_confirmation(analysis)
        if confirmation != "CONFIRMED":
            return None

        verdict = analysis.get("verdict", "")
        if verdict in _cfg.BEARISH_VERDICTS:
            return "BUY_PUT"
        if verdict in _cfg.BULLISH_VERDICTS:
            return "BUY_CALL"
        return None

    def _create(self, direction: str, analysis: dict, strikes_data: dict) -> Optional[int]:
        spot = analysis.get("spot_price", 0)
        verdict = analysis.get("verdict", "")
        confidence = analysis.get("signal_confidence", 0)
        iv_skew = analysis.get("iv_skew", 0) or 0
        vix = analysis.get("vix", 0) or 0
        combined_score = self._get_combined_score(analysis)
        confirmation = self._get_confirmation(analysis)

        step = _mkt.NIFTY_STEP
        strike = round(spot / step) * step
        option_type = "PE" if direction == "BUY_PUT" else "CE"

        strike_data = strikes_data.get(strike, {})
        entry = strike_data.get("pe_ltp" if option_type == "PE" else "ce_ltp", 0)
        if not entry or entry < _cfg.MIN_PREMIUM:
            log.warning("Momentum skipped: premium too low", strike=strike, premium=entry)
            return None

        sl = round(entry * (1 - _cfg.SL_PCT / 100), 2)
        target = round(entry * (1 + _cfg.TARGET_PCT / 100), 2)

        now = datetime.now()
        trade_id = self.trade_repo.insert_trade(
            self.table_name,
            created_at=now,
            strategy_name=_cfg.STRATEGY_NAME,
            direction=direction,
            strike=strike,
            option_type=option_type,
            entry_premium=entry,
            sl_premium=sl,
            target_premium=target,
            spot_at_creation=spot,
            verdict_at_creation=verdict,
            signal_confidence=confidence,
            iv_skew_at_creation=iv_skew,
            vix_at_creation=vix,
            combined_score=combined_score,
            confirmation_status=confirmation,
            status="ACTIVE",
            max_premium_reached=entry,
            min_premium_reached=entry,
        )

        log.info("Created momentum trade", trade_id=trade_id, direction=direction,
                 strike=f"{strike} {option_type}", entry=entry, sl=sl, target=target)

        self._publish(EventType.TRADE_CREATED, {
            "trade_id": trade_id, "direction": direction, "strike": strike,
            "option_type": option_type, "entry": entry, "sl": sl, "target": target,
            "alert_message": self._format_entry_alert(
                direction, strike, option_type, entry, sl, target,
                spot, verdict, confidence, iv_skew, vix, combined_score),
        })
        return trade_id

    def _check(self, strikes_data: dict) -> Optional[Dict]:
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
            log.info(f"Momentum {status}", pnl=f"{pnl:.2f}%", entry=entry, exit=current)
            self._publish(EventType.TRADE_EXITED, {
                "trade_id": trade["id"], "action": status, "pnl": pnl, "reason": reason,
                "alert_message": self._format_exit_alert(trade, current, reason, pnl),
            })
            return {"action": status, "pnl": pnl, "reason": reason,
                    "strategy": _cfg.STRATEGY_NAME}

        if current <= trade["sl_premium"]:
            return _resolve("LOST", "SL")
        if current >= trade["target_premium"]:
            return _resolve("WON", "TARGET")
        if self.is_past_force_close(now):
            pnl = ((current - entry) / entry) * 100
            return _resolve("WON" if pnl > 0 else "LOST", "EOD")
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_confirmation(analysis: dict) -> str:
        aj = analysis.get("analysis_json", "")
        if isinstance(aj, str) and aj:
            try:
                return json.loads(aj).get("confirmation_status", "")
            except (json.JSONDecodeError, TypeError):
                pass
        elif isinstance(aj, dict):
            return aj.get("confirmation_status", "")
        return analysis.get("confirmation_status", "")

    @staticmethod
    def _get_combined_score(analysis: dict) -> float:
        aj = analysis.get("analysis_json", "")
        if isinstance(aj, str) and aj:
            try:
                return json.loads(aj).get("combined_score", 0)
            except (json.JSONDecodeError, TypeError):
                pass
        elif isinstance(aj, dict):
            return aj.get("combined_score", 0)
        return analysis.get("combined_score", 0)

    @staticmethod
    def _format_entry_alert(direction, strike, option_type, entry, sl,
                            target, spot, verdict, confidence, iv_skew, vix,
                            combined_score) -> str:
        dir_emoji = "\U0001f534" if direction == "BUY_PUT" else "\U0001f7e2"
        dir_text = "BUY PUT" if direction == "BUY_PUT" else "BUY CALL"
        return (
            f"<b>\U0001f680 MOMENTUM: {dir_text}</b>\n\n"
            f"<b>Direction:</b> <code>{dir_text}</code> {dir_emoji}\n"
            f"<b>Strike:</b> <code>{strike} {option_type}</code>\n"
            f"<b>Spot:</b> <code>{spot:.2f}</code>\n"
            f"<b>Entry:</b> <code>Rs {entry:.2f}</code>\n"
            f"<b>SL:</b> <code>Rs {sl:.2f}</code> (-{_cfg.SL_PCT:.0f}%)\n"
            f"<b>Target:</b> <code>Rs {target:.2f}</code> (+{_cfg.TARGET_PCT:.0f}%)\n"
            f"<b>RR:</b> <code>1:2</code>\n\n"
            f"<b>Why:</b> Triple alignment \u2014 OI, verdict, and price all agree\n\n"
            f"<b>Verdict:</b> {verdict} ({confidence:.0f}%)\n"
            f"<b>Score:</b> {combined_score:+.1f} | <b>Status:</b> CONFIRMED\n"
            f"<b>VIX:</b> {vix:.1f} | <b>IV Skew:</b> {iv_skew:.2f}\n\n"
            f"<i>Trend-following 1:2 RR \u2014 riding the momentum</i>\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )

    @staticmethod
    def _format_exit_alert(trade, exit_premium, reason, pnl) -> str:
        result_emoji = "\u2705" if pnl > 0 else "\u274c"
        dir_emoji = "\U0001f534" if trade.get("direction") == "BUY_PUT" else "\U0001f7e2"
        reason_text = {
            "TARGET": f"Target Hit (+{_cfg.TARGET_PCT:.0f}%)",
            "SL": f"Stop Loss (-{_cfg.SL_PCT:.0f}%)",
            "EOD": "End of Day",
        }.get(reason, reason)
        return (
            f"<b>{result_emoji} \U0001f680 Momentum {'WON' if pnl > 0 else 'LOST'}</b> {dir_emoji}\n\n"
            f"<b>Strike:</b> <code>{trade['strike']} {trade['option_type']}</code>\n"
            f"<b>Entry:</b> <code>Rs {trade['entry_premium']:.2f}</code>\n"
            f"<b>Exit:</b> <code>Rs {exit_premium:.2f}</code>\n"
            f"<b>P&L:</b> <code>{pnl:+.2f}%</code>\n"
            f"<b>Exit:</b> {reason_text}\n\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )
