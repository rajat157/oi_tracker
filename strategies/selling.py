"""Selling strategy — options selling with dual T1/T2 targets.

Fully self-contained: all logic absorbed from selling_tracker.py.

Signal Logic:
- Verdict: "Slightly Bullish" or "Slightly Bearish" only
- Confidence >= 65%, one trade per day
- Slightly Bullish -> SELL OTM PUT | Slightly Bearish -> SELL OTM CALL

Selling-specific:
- P&L is inverted: premium dropping = profit
- T1 at -25% (notify only), T2 at -50% (auto-exit)
- Uses send_telegram_multi for external user alerts
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from config import SellingConfig, MarketConfig
from core.base_tracker import BaseTracker
from core.events import EventType
from db.schema import SELL_TRADE_SETUPS_DDL
from core.logger import get_logger

log = get_logger("selling_tracker")

_cfg = SellingConfig()
_mkt = MarketConfig()


class SellingStrategy(BaseTracker):
    tracker_type = "selling"
    table_name = "sell_trade_setups"
    time_start = _cfg.TIME_START
    time_end = _cfg.TIME_END
    force_close_time = _cfg.FORCE_CLOSE_TIME
    max_trades_per_day = 1
    is_selling = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.trade_repo:
            self.trade_repo.init_table(SELL_TRADE_SETUPS_DDL)

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    def should_create(self, analysis: dict, **kwargs) -> bool:
        if self.trade_repo:
            if self.trade_repo.get_todays_trades(self.table_name):
                return False
            if self.trade_repo.get_active(self.table_name):
                return False
        elif self.already_traded_today():
            return False

        if not self.is_in_time_window():
            return False

        verdict = analysis.get("verdict", "")
        if "Slightly" not in verdict:
            return False

        confidence = analysis.get("signal_confidence", 0)
        if confidence < _cfg.MIN_CONFIDENCE:
            return False

        log.info("Sell signal valid", verdict=verdict,
                 confidence=f"{confidence:.0f}%")
        return True

    def create_trade(self, signal: Any, analysis: dict, strikes_data: dict,
                     **kwargs) -> Optional[int]:
        verdict = analysis.get("verdict", "")
        spot = analysis.get("spot_price", 0)
        confidence = analysis.get("signal_confidence", 0)

        if "Bullish" in verdict:
            direction = "SELL_PUT"
            option_type = "PE"
        else:
            direction = "SELL_CALL"
            option_type = "CE"

        strike = self.get_otm_strike(spot, direction)

        strike_data = strikes_data.get(strike, {})
        if option_type == "PE":
            entry = strike_data.get("pe_ltp", 0)
            iv = strike_data.get("pe_iv", 0)
        else:
            entry = strike_data.get("ce_ltp", 0)
            iv = strike_data.get("ce_iv", 0)

        if not entry or entry < _cfg.MIN_PREMIUM:
            log.warning("Sell setup skipped: premium too low",
                        strike=strike, premium=entry)
            return None

        sl = round(entry * (1 + _cfg.SL_PCT / 100), 2)
        t1 = round(entry * (1 - _cfg.TARGET1_PCT / 100), 2)
        t2 = round(entry * (1 - _cfg.TARGET2_PCT / 100), 2)

        now = datetime.now()
        trade_id = self.trade_repo.insert_trade(
            self.table_name,
            created_at=now,
            direction=direction,
            strike=strike,
            option_type=option_type,
            entry_premium=entry,
            sl_premium=sl,
            target_premium=t1,
            target2_premium=t2,
            spot_at_creation=spot,
            verdict_at_creation=verdict,
            signal_confidence=confidence,
            iv_at_creation=iv,
            status="ACTIVE",
            max_premium_reached=entry,
            min_premium_reached=entry,
        )

        log.info("Created SELL setup", setup_id=trade_id, direction=direction,
                 strike=f"{strike} {option_type}",
                 entry=entry, sl=sl, t1=t1, t2=t2)

        self._publish(EventType.TRADE_CREATED, {
            "trade_id": trade_id, "direction": direction, "strike": strike,
            "option_type": option_type, "entry": entry, "sl": sl,
            "alert_message": self._format_entry_alert(
                direction, strike, option_type, entry, sl, t1, t2,
                verdict, confidence, spot),
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

        # SL: premium rises = loss for seller
        if current >= trade["sl_premium"]:
            pnl = ((entry - current) / entry) * 100
            self.trade_repo.update_trade(
                self.table_name, trade["id"],
                status="LOST", resolved_at=now,
                exit_premium=current, exit_reason="SL",
                profit_loss_pct=pnl,
            )
            log.info("SELL trade LOST (SL hit)", pnl=f"{pnl:.2f}%",
                     entry=entry, exit=current)
            self._publish(EventType.TRADE_EXITED, {
                "trade_id": trade["id"], "action": "LOST", "pnl": pnl,
                "reason": "SL",
                "alert_message": self._format_exit_alert(trade, current, "SL", pnl),
            })
            return {"action": "LOST", "pnl": pnl, "reason": "SL"}

        # T1 hit: notify but don't exit
        if not trade.get("t1_hit") and current <= trade["target_premium"]:
            self.trade_repo.update_trade(
                self.table_name, trade["id"], t1_hit=1, t1_hit_at=now,
            )
            log.info("SELL trade T1 HIT", entry=entry, current=current)
            pnl = ((entry - current) / entry) * 100
            self._publish(EventType.T1_HIT, {
                "trade_id": trade["id"], "pnl": pnl,
                "alert_message": self._format_t1_alert(trade, current),
            })

        # T2: full profit for seller
        t2 = trade.get("target2_premium") or trade["target_premium"]
        if current <= t2:
            pnl = ((entry - current) / entry) * 100
            self.trade_repo.update_trade(
                self.table_name, trade["id"],
                status="WON", resolved_at=now,
                exit_premium=current, exit_reason="TARGET2",
                profit_loss_pct=pnl,
            )
            log.info("SELL trade WON (T2 hit)", pnl=f"{pnl:.2f}%",
                     entry=entry, exit=current)
            self._publish(EventType.TRADE_EXITED, {
                "trade_id": trade["id"], "action": "WON", "pnl": pnl,
                "reason": "TARGET2",
                "alert_message": self._format_exit_alert(trade, current, "TARGET2", pnl),
            })
            return {"action": "WON", "pnl": pnl, "reason": "TARGET2"}

        # EOD exit
        if self.is_past_force_close(now):
            pnl = ((entry - current) / entry) * 100
            status = "WON" if pnl > 0 else "LOST"
            self.trade_repo.update_trade(
                self.table_name, trade["id"],
                status=status, resolved_at=now,
                exit_premium=current, exit_reason="EOD",
                profit_loss_pct=pnl,
            )
            log.info(f"SELL trade {status} (EOD)", pnl=f"{pnl:.2f}%")
            self._publish(EventType.TRADE_EXITED, {
                "trade_id": trade["id"], "action": status, "pnl": pnl,
                "reason": "EOD",
                "alert_message": self._format_exit_alert(trade, current, "EOD", pnl),
            })
            return {"action": status, "pnl": pnl, "reason": "EOD"}

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
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_otm_strike(spot_price: float, direction: str) -> int:
        """Get OTM strike for selling."""
        atm = round(spot_price / _mkt.NIFTY_STEP) * _mkt.NIFTY_STEP
        if direction == "SELL_PUT":
            return atm - (_mkt.NIFTY_STEP * _cfg.OTM_OFFSET)
        else:  # SELL_CALL
            return atm + (_mkt.NIFTY_STEP * _cfg.OTM_OFFSET)

    # ------------------------------------------------------------------
    # Alert formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_entry_alert(direction, strike, option_type, entry, sl, t1, t2,
                            verdict, confidence, spot) -> str:
        dir_text = "SELL PUT" if direction == "SELL_PUT" else "SELL CALL"
        emoji = "\U0001f534" if "CALL" in direction else "\U0001f7e2"
        return (
            f"<b>{emoji} SELL SETUP</b>\n\n"
            f"<b>Direction:</b> <code>{dir_text}</code>\n"
            f"<b>Strike:</b> <code>{strike} {option_type}</code>\n"
            f"<b>Spot:</b> <code>{spot:.2f}</code>\n"
            f"<b>Premium Collected:</b> <code>Rs {entry:.2f}</code>\n"
            f"<b>SL (buyback):</b> <code>Rs {sl:.2f}</code> (+{_cfg.SL_PCT:.0f}% rise)\n"
            f"<b>T1 (1:1):</b> <code>Rs {t1:.2f}</code> (-{_cfg.TARGET1_PCT:.0f}% drop)\n"
            f"<b>T2 (1:2):</b> <code>Rs {t2:.2f}</code> (-{_cfg.TARGET2_PCT:.0f}% drop)\n\n"
            f"<b>Verdict:</b> {verdict}\n"
            f"<b>Confidence:</b> {confidence:.0f}%\n\n"
            f"<i>Auto-exits at T2. Take T1 manually if you prefer 1:1.</i>\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )

    @staticmethod
    def _format_t1_alert(trade, current_premium) -> str:
        pnl = ((trade["entry_premium"] - current_premium) / trade["entry_premium"]) * 100
        dir_text = "SELL PUT" if trade["direction"] == "SELL_PUT" else "SELL CALL"
        return (
            f"<b>\U0001f3af T1 HIT \u2014 SELL TRADE</b>\n\n"
            f"<b>Direction:</b> <code>{dir_text}</code>\n"
            f"<b>Strike:</b> <code>{trade['strike']} {trade['option_type']}</code>\n"
            f"<b>Entry:</b> <code>Rs {trade['entry_premium']:.2f}</code>\n"
            f"<b>Current:</b> <code>Rs {current_premium:.2f}</code>\n"
            f"<b>P&L:</b> <code>{pnl:+.1f}%</code> (1:1 RR)\n\n"
            f"<i>Book profit now or let it ride to T2 ({_cfg.TARGET2_PCT:.0f}% drop)</i>\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )

    @staticmethod
    def _format_exit_alert(trade, exit_premium, reason, pnl) -> str:
        emoji = "\u2705" if pnl > 0 else "\u274c"
        dir_text = "SELL PUT" if trade["direction"] == "SELL_PUT" else "SELL CALL"
        t1_status = "\u2705 Hit" if trade.get("t1_hit") else "\u274c Missed"
        reason_text = {
            "TARGET2": "T2 Target Hit (1:2 RR)",
            "SL": "Stop Loss",
            "EOD": "End of Day",
        }.get(reason, reason)
        return (
            f"<b>{emoji} SELL TRADE {'WON' if pnl > 0 else 'LOST'}</b>\n\n"
            f"<b>Direction:</b> <code>{dir_text}</code>\n"
            f"<b>Strike:</b> <code>{trade['strike']} {trade['option_type']}</code>\n"
            f"<b>Entry Premium:</b> <code>Rs {trade['entry_premium']:.2f}</code>\n"
            f"<b>Exit Premium:</b> <code>Rs {exit_premium:.2f}</code>\n"
            f"<b>P&L:</b> <code>{pnl:+.2f}%</code>\n"
            f"<b>Exit:</b> {reason_text}\n"
            f"<b>T1 (1:1):</b> {t1_status}\n\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )
