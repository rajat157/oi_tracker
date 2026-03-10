"""Dessert strategy — premium 1:2 RR (Contra Sniper + Phantom PUT).

Fully self-contained: no legacy dessert_tracker.py imports.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from config import DessertConfig, MarketConfig
from core.base_tracker import BaseTracker
from core.events import EventType
from db.schema import DESSERT_TRADES_DDL
from logger import get_logger

log = get_logger("dessert_tracker")

_cfg = DessertConfig()
_mkt = MarketConfig()


class DessertStrategy(BaseTracker):
    tracker_type = "dessert"
    table_name = "dessert_trades"
    time_start = _cfg.TIME_START
    time_end = _cfg.TIME_END
    force_close_time = _cfg.FORCE_CLOSE_TIME
    max_trades_per_day = 1
    is_selling = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.trade_repo:
            self.trade_repo.init_table(DESSERT_TRADES_DDL)

    # --- Abstract interface ---

    def should_create(self, analysis: dict, **kwargs) -> bool:
        return self.evaluate(analysis) is not None

    def evaluate(self, analysis: dict) -> Optional[str]:
        """Return strategy name ('Contra Sniper' / 'Phantom PUT') or None."""
        if self.trade_repo:
            if self.trade_repo.get_todays_trades(self.table_name):
                return None
            if self.trade_repo.get_active(self.table_name):
                return None
        elif self.already_traded_today():
            return None

        if not self.is_in_time_window():
            return None

        spot_move = self._get_spot_move_30m()

        if self._check_contra_sniper(analysis):
            return _cfg.CONTRA_SNIPER
        if self._check_phantom_put(analysis, spot_move):
            return _cfg.PHANTOM_PUT
        return None

    def create_trade(self, signal: Any, analysis: dict, strikes_data: dict,
                     **kwargs) -> Optional[int]:
        strategy_name = signal if isinstance(signal, str) else str(signal)
        return self._create(strategy_name, analysis, strikes_data)

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

    # --- Strategy checks ---

    @staticmethod
    def _check_contra_sniper(analysis: dict) -> bool:
        verdict = analysis.get("verdict", "")
        iv_skew = analysis.get("iv_skew", 0) or 0
        spot = analysis.get("spot_price", 0)
        max_pain = analysis.get("max_pain", 0) or 0
        if "Bull" not in verdict:
            return False
        if iv_skew >= 1:
            return False
        atm = round(spot / _mkt.NIFTY_STEP) * _mkt.NIFTY_STEP
        if max_pain <= 0 or atm >= max_pain:
            return False
        return True

    @staticmethod
    def _check_phantom_put(analysis: dict, spot_move: Optional[float]) -> bool:
        confidence = analysis.get("signal_confidence", 0) or 0
        iv_skew = analysis.get("iv_skew", 0) or 0
        if confidence >= 50:
            return False
        if iv_skew >= 0:
            return False
        if spot_move is None or spot_move <= 0.05:
            return False
        return True

    def _get_spot_move_30m(self) -> Optional[float]:
        """Calculate spot price movement over last 30 minutes from analysis_history."""
        if self.trade_repo is None:
            return None
        now = datetime.now()
        thirty_ago = now - timedelta(minutes=30)
        rows = self.trade_repo._fetch_all(
            "SELECT spot_price FROM analysis_history "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (thirty_ago.strftime('%Y-%m-%d %H:%M:%S'),
             now.strftime('%Y-%m-%d %H:%M:%S')),
        )
        if len(rows) < 2:
            return None
        first, last = rows[0]["spot_price"], rows[-1]["spot_price"]
        return (last - first) / first * 100

    # --- Create/check ---

    def _create(self, strategy_name: str, analysis: dict, strikes_data: dict) -> Optional[int]:
        spot = analysis.get("spot_price", 0)
        verdict = analysis.get("verdict", "")
        confidence = analysis.get("signal_confidence", 0)
        iv_skew = analysis.get("iv_skew", 0) or 0
        vix = analysis.get("vix", 0) or 0
        max_pain = analysis.get("max_pain", 0) or 0

        strike = round(spot / _mkt.NIFTY_STEP) * _mkt.NIFTY_STEP
        strike_data = strikes_data.get(strike, {})
        entry = strike_data.get("pe_ltp", 0)
        if not entry or entry < _cfg.MIN_PREMIUM:
            log.warning(f"Dessert ({strategy_name}) skipped: premium too low",
                        strike=strike, premium=entry)
            return None

        sl = round(entry * (1 - _cfg.SL_PCT / 100), 2)
        target = round(entry * (1 + _cfg.TARGET_PCT / 100), 2)
        spot_move = self._get_spot_move_30m()

        now = datetime.now()
        trade_id = self.trade_repo.insert_trade(
            self.table_name,
            strategy_name=strategy_name,
            created_at=now,
            direction="BUY_PUT",
            strike=strike,
            option_type="PE",
            entry_premium=entry,
            sl_premium=sl,
            target_premium=target,
            spot_at_creation=spot,
            verdict_at_creation=verdict,
            signal_confidence=confidence,
            iv_skew_at_creation=iv_skew,
            vix_at_creation=vix,
            max_pain_at_creation=max_pain,
            spot_move_30m=spot_move or 0,
            status="ACTIVE",
            max_premium_reached=entry,
            min_premium_reached=entry,
        )

        log.info(f"Created dessert trade: {strategy_name}", trade_id=trade_id,
                 strike=f"{strike} PE", entry=entry, sl=sl, target=target)
        self._publish(EventType.TRADE_CREATED, {
            "trade_id": trade_id, "strategy": strategy_name,
            "strike": strike, "entry": entry,
            "alert_message": self._format_entry_alert(
                strategy_name, strike, entry, sl, target,
                spot, verdict, confidence, iv_skew, vix, max_pain, spot_move),
        })
        return trade_id

    def _check(self, strikes_data: dict) -> Optional[Dict]:
        trade = self.get_active()
        if not trade:
            return None

        strike, option_type = trade["strike"], trade["option_type"]
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
            log.info(f"Dessert {status} ({trade['strategy_name']})",
                     pnl=f"{pnl:.2f}%", entry=entry, exit=current)
            self._publish(EventType.TRADE_EXITED, {
                "trade_id": trade["id"], "action": status, "pnl": pnl, "reason": reason,
                "alert_message": self._format_exit_alert(trade, current, reason, pnl),
            })
            return {"action": status, "pnl": pnl, "reason": reason,
                    "strategy": trade["strategy_name"]}

        if current <= trade["sl_premium"]:
            return _resolve("LOST", "SL")
        if current >= trade["target_premium"]:
            return _resolve("WON", "TARGET")
        if self.is_past_force_close(now):
            pnl = ((current - entry) / entry) * 100
            return _resolve("WON" if pnl > 0 else "LOST", "EOD")
        return None

    # --- Alerts ---

    @staticmethod
    def _format_entry_alert(strategy, strike, entry, sl, target,
                            spot, verdict, confidence, iv_skew, vix, max_pain,
                            spot_move=None) -> str:
        emoji = "\U0001f3af" if strategy == _cfg.CONTRA_SNIPER else "\U0001f52e"
        desc = ("Crowd says Bullish, but price below Max Pain + low IV skew = reversal incoming"
                if strategy == _cfg.CONTRA_SNIPER
                else "Low confidence + negative IV skew + rising spot = hidden reversal")
        return (
            f"<b>{emoji} DESSERT: {strategy}</b>\n\n"
            f"<b>Direction:</b> <code>BUY PUT</code>\n"
            f"<b>Strike:</b> <code>{strike} PE</code>\n"
            f"<b>Spot:</b> <code>{spot:.2f}</code>\n"
            f"<b>Entry:</b> <code>Rs {entry:.2f}</code>\n"
            f"<b>SL:</b> <code>Rs {sl:.2f}</code> (-{_cfg.SL_PCT:.0f}%)\n"
            f"<b>Target:</b> <code>Rs {target:.2f}</code> (+{_cfg.TARGET_PCT:.0f}%)\n"
            f"<b>RR:</b> <code>1:2</code>\n\n"
            f"<b>Why:</b> {desc}\n\n"
            f"<b>Verdict:</b> {verdict} ({confidence:.0f}%)\n"
            f"<b>VIX:</b> {vix:.1f} | <b>IV Skew:</b> {iv_skew:.2f}\n"
            f"<b>Max Pain:</b> {max_pain:.0f}\n"
            f"<b>Spot 30m:</b> {(spot_move or 0):.3f}%\n\n"
            f"<i>This is a 1:2 RR dessert trade. Take it if it looks good!</i>\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )

    @staticmethod
    def _format_exit_alert(trade, exit_premium, reason, pnl) -> str:
        strategy = trade["strategy_name"]
        emoji = "\U0001f3af" if strategy == _cfg.CONTRA_SNIPER else "\U0001f52e"
        result_emoji = "\u2705" if pnl > 0 else "\u274c"
        reason_text = {"TARGET": "Target Hit (+50%)", "SL": "Stop Loss (-25%)",
                       "EOD": "End of Day"}.get(reason, reason)
        return (
            f"<b>{result_emoji} {emoji} {strategy} {'WON' if pnl > 0 else 'LOST'}</b>\n\n"
            f"<b>Strike:</b> <code>{trade['strike']} PE</code>\n"
            f"<b>Entry:</b> <code>Rs {trade['entry_premium']:.2f}</code>\n"
            f"<b>Exit:</b> <code>Rs {exit_premium:.2f}</code>\n"
            f"<b>P&L:</b> <code>{pnl:+.2f}%</code>\n"
            f"<b>Exit:</b> {reason_text}\n\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )
