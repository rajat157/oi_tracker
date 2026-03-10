"""PulseRider strategy — ATM premium price action (CHC-3).

Fully self-contained: all logic absorbed from pa_tracker.py.
Stateful: ATM strike locked at market open, premium candle history.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import PulseRiderConfig, MarketConfig
from core.base_tracker import BaseTracker
from core.events import EventType
from db.schema import PA_TRADES_DDL
from logger import get_logger

log = get_logger("pa_tracker")

_cfg = PulseRiderConfig()
_mkt = MarketConfig()

# Order placement config (from .env)
_PA_PLACE_ORDER = os.getenv("PA_PLACE_ORDER", "false").lower() == "true"
_PA_LOTS = int(os.getenv("PA_LOTS", "1"))


class PulseRiderStrategy(BaseTracker):
    tracker_type = "pulse_rider"
    table_name = "pa_trades"
    time_start = _cfg.TIME_START
    time_end = _cfg.TIME_END
    force_close_time = _cfg.FORCE_CLOSE_TIME
    max_trades_per_day = 1
    is_selling = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.trade_repo:
            self.trade_repo.init_table(PA_TRADES_DDL)
        # Stateful fields
        self.atm_strike: Optional[int] = None
        self.premium_history: List[Dict] = []
        self._current_date = None

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    def should_create(self, analysis: dict, **kwargs) -> bool:
        strikes_data = kwargs.get("strikes_data", {})
        return self.evaluate(analysis, strikes_data) is not None

    def evaluate(self, analysis: dict, strikes_data: dict) -> Optional[str]:
        """Return 'CE' or 'PE' if CHC-3 detected, else None."""
        now = datetime.now()

        # Auto-reset on new day
        today = now.date()
        if self._current_date != today:
            self.reset_day()
            self._current_date = today

        # One trade per day + no active
        if self.trade_repo:
            if self.trade_repo.get_todays_trades(self.table_name):
                return None
            if self.trade_repo.get_active(self.table_name):
                return None

        if not self.is_in_time_window(now):
            return None

        spot_price = analysis.get("spot_price", 0)
        if spot_price <= 0:
            return None

        # Lock ATM if not locked
        if not self.atm_strike:
            self._lock_atm_strike(spot_price)

        # Record premium
        self._record_premium(strikes_data, now, spot_price)

        if len(self.premium_history) < _cfg.CHC_LOOKBACK + 1:
            return None

        signal = self._detect_momentum()
        if not signal:
            return None

        side, strength = signal

        # IV Skew filter
        iv_skew = analysis.get("iv_skew", 0) or 0
        if not self._is_iv_skew_ok(side, iv_skew):
            log.info("PA skipped: IV skew filter", side=side, iv_skew=f"{iv_skew:.2f}")
            return None

        # Choppy filter
        if self._is_choppy():
            log.info("PA skipped: choppy market")
            return None

        # CONFLICT confirmation filter
        confirmation = analysis.get("confirmation_status", "")
        if confirmation == "CONFLICT":
            log.info("PA skipped: CONFLICT confirmation")
            return None

        log.info("PA CHC(3) detected", side=side, strength=f"{strength:.2%}")
        return side

    def create_trade(self, signal: Any, analysis: dict, strikes_data: dict,
                     **kwargs) -> Optional[int]:
        side = signal if isinstance(signal, str) else str(signal)
        return self._create(side, analysis, strikes_data)

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
            log.info(f"PA {status} ({reason})", pnl=f"{pnl:.2f}%",
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
            pnl = ((current - entry) / entry) * 100
            return _resolve("WON" if pnl > 0 else "LOST", "EOD")
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
    # PulseRider-specific public interface
    # ------------------------------------------------------------------

    def record_premium(self, strikes_data: dict, timestamp: datetime, spot_price: float):
        """Record ATM premium candle (called every cycle even without trade)."""
        self._record_premium(strikes_data, timestamp, spot_price)

    def reset_day(self):
        """Reset state for a new trading day."""
        self.atm_strike = None
        self.premium_history = []
        log.info("PA tracker reset for new day")

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _lock_atm_strike(self, spot_price: float):
        self.atm_strike = round(spot_price / _mkt.NIFTY_STEP) * _mkt.NIFTY_STEP
        log.info("PA ATM strike locked", strike=self.atm_strike, spot=f"{spot_price:.2f}")

    def _record_premium(self, strikes_data: dict, timestamp: datetime, spot_price: float):
        if not self.atm_strike:
            return
        strike_data = strikes_data.get(self.atm_strike, {})
        ce_ltp = strike_data.get("ce_ltp", 0)
        pe_ltp = strike_data.get("pe_ltp", 0)
        if ce_ltp <= 0 or pe_ltp <= 0:
            return
        self.premium_history.append({
            "ts": timestamp, "ce_ltp": ce_ltp, "pe_ltp": pe_ltp,
            "spot": spot_price,
        })

    def _detect_momentum(self) -> Optional[tuple]:
        """Detect CHC(3) — 3 consecutive higher closes on CE or PE."""
        n = len(self.premium_history)
        lookback = _cfg.CHC_LOOKBACK
        if n < lookback + 1:
            return None

        recent = self.premium_history[-(lookback + 1):]

        ce_rising = all(
            recent[i]["ce_ltp"] > recent[i - 1]["ce_ltp"]
            for i in range(1, lookback + 1)
        )
        pe_rising = all(
            recent[i]["pe_ltp"] > recent[i - 1]["pe_ltp"]
            for i in range(1, lookback + 1)
        )

        ce_start = recent[0]["ce_ltp"]
        pe_start = recent[0]["pe_ltp"]
        ce_pct = ((recent[-1]["ce_ltp"] - ce_start) / ce_start) if ce_start > 0 else 0
        pe_pct = ((recent[-1]["pe_ltp"] - pe_start) / pe_start) if pe_start > 0 else 0

        if ce_rising and not pe_rising:
            return ("CE", ce_pct)
        elif pe_rising and not ce_rising:
            return ("PE", pe_pct)
        elif ce_rising and pe_rising:
            return ("CE", ce_pct) if ce_pct > pe_pct else ("PE", pe_pct)
        return None

    @staticmethod
    def _is_iv_skew_ok(side: str, iv_skew: float) -> bool:
        if side == "CE" and iv_skew > 1.0:
            return False
        if side == "PE" and iv_skew < -1.0:
            return False
        return True

    def _is_choppy(self) -> bool:
        choppy_lookback = _cfg.CHOPPY_LOOKBACK
        if len(self.premium_history) < choppy_lookback + 1:
            return False
        recent_spots = [c["spot"] for c in self.premium_history[-(choppy_lookback + 1):]]
        avg_spot = sum(recent_spots) / len(recent_spots)
        if avg_spot <= 0:
            return False
        spot_range_pct = ((max(recent_spots) - min(recent_spots)) / avg_spot) * 100
        return spot_range_pct < _cfg.CHOPPY_THRESHOLD

    # ------------------------------------------------------------------
    # Trade creation
    # ------------------------------------------------------------------

    def _create(self, side: str, analysis: dict, strikes_data: dict) -> Optional[int]:
        spot = analysis.get("spot_price", 0)
        verdict = analysis.get("verdict", "")
        confidence = analysis.get("signal_confidence", 0)
        iv_skew = analysis.get("iv_skew", 0) or 0
        vix = analysis.get("vix", 0) or 0

        option_type = side
        direction = "BUY_CALL" if side == "CE" else "BUY_PUT"

        strike_data = strikes_data.get(self.atm_strike, {})
        entry = strike_data.get("ce_ltp" if side == "CE" else "pe_ltp", 0)

        if not entry or entry < _cfg.MIN_PREMIUM:
            log.warning("PA skipped: premium too low",
                        strike=self.atm_strike, premium=entry)
            return None
        if entry > _cfg.MAX_PREMIUM:
            log.info("PA skipped: premium too high",
                     strike=self.atm_strike, premium=entry)
            return None

        sl = round(entry * (1 - _cfg.SL_PCT / 100), 2)
        target = round(entry * (1 + _cfg.TARGET_PCT / 100), 2)

        signal = self._detect_momentum()
        chc_strength = signal[1] if signal else 0

        now = datetime.now()
        trade_id = self.trade_repo.insert_trade(
            self.table_name,
            created_at=now,
            direction=direction,
            strike=self.atm_strike,
            option_type=option_type,
            entry_premium=entry,
            sl_premium=sl,
            target_premium=target,
            spot_at_creation=spot,
            verdict_at_creation=verdict,
            signal_confidence=confidence,
            iv_skew_at_creation=iv_skew,
            vix_at_creation=vix,
            chc_strength=chc_strength,
            status="ACTIVE",
            max_premium_reached=entry,
            min_premium_reached=entry,
        )

        log.info("Created PA trade",
                 trade_id=trade_id, direction=direction,
                 strike=f"{self.atm_strike} {option_type}",
                 entry=entry, sl=sl, target=target)

        self._publish(EventType.TRADE_CREATED, {
            "trade_id": trade_id, "direction": direction, "strike": self.atm_strike,
            "option_type": option_type, "entry": entry, "sl": sl, "target": target,
            "alert_message": self._format_entry_alert(
                direction, self.atm_strike, option_type, entry, sl, target,
                spot, verdict, confidence, iv_skew, vix, chc_strength),
        })

        # Auto-place Kite order if enabled (skip on high VIX)
        is_paper = vix > _cfg.VIX_WARN_THRESHOLD
        if _PA_PLACE_ORDER and not is_paper:
            self._place_kite_order(
                trade_id, self.atm_strike, option_type,
                entry, sl, target,
                analysis.get("expiry_date", ""),
            )
        elif is_paper:
            log.info("PA paper trade: VIX too high", vix=f"{vix:.1f}")

        return trade_id

    def _place_kite_order(self, trade_id: int, strike: int, option_type: str,
                          entry: float, sl: float, target: float, expiry_date: str):
        try:
            from kite_broker import is_authenticated, place_order, place_gtt_oco, round_to_tick
            from alerts import _get_kite_trading_symbol, send_telegram

            if not is_authenticated():
                log.warning("PA order skipped: Kite not authenticated")
                return

            trading_symbol = _get_kite_trading_symbol(strike, option_type, expiry_date)
            quantity = _PA_LOTS * _mkt.NIFTY_LOT_SIZE

            entry_r = round_to_tick(entry, "nearest")
            sl_r = round_to_tick(sl, "down")
            target_r = round_to_tick(target, "up")

            order_result = place_order(
                trading_symbol=trading_symbol, transaction_type="BUY",
                quantity=quantity, price=entry_r,
                order_type="LIMIT", product="NRML",
            )
            if order_result.get("status") != "success":
                log.error("PA buy order failed", result=order_result)
                return

            order_id = order_result["data"]["order_id"]
            gtt_result = place_gtt_oco(
                trading_symbol=trading_symbol, entry_price=entry_r,
                sl_price=sl_r, target_price=target_r,
                quantity=quantity, product="NRML",
            )
            trigger_id = gtt_result.get("data", {}).get("trigger_id") if gtt_result.get("status") == "success" else None
            log.info("PA order placed", order_id=order_id, trigger_id=trigger_id)
        except Exception as e:
            log.error("PA auto order placement error", error=str(e))

    # ------------------------------------------------------------------
    # Alert formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_entry_alert(direction, strike, option_type, entry, sl, target,
                            spot, verdict, confidence, iv_skew, vix,
                            chc_strength) -> str:
        side_emoji = "\U0001f7e2" if option_type == "CE" else "\U0001f534"
        vix_warning = ""
        if vix > _cfg.VIX_WARN_THRESHOLD:
            vix_warning = (
                f"\n\u26a0\ufe0f <b>HIGH VIX ({vix:.1f}) \u2014 PAPER TRADE ONLY</b>\n"
                f"<i>Kite order NOT placed. Tracking for data collection.</i>\n"
            )
        return (
            f"<b>{side_emoji} PRICE ACTION: {direction}</b>\n"
            f"{vix_warning}\n"
            f"<b>Signal:</b> CHC(3) on {option_type} ({chc_strength:.1%} move)\n"
            f"<b>Strike:</b> <code>{strike} {option_type}</code>\n"
            f"<b>Spot:</b> <code>{spot:.2f}</code>\n"
            f"<b>Entry:</b> <code>Rs {entry:.2f}</code>\n"
            f"<b>SL:</b> <code>Rs {sl:.2f}</code> (-{_cfg.SL_PCT:.0f}%)\n"
            f"<b>Target:</b> <code>Rs {target:.2f}</code> (+{_cfg.TARGET_PCT:.0f}%)\n"
            f"<b>RR:</b> <code>1:1</code>\n\n"
            f"<b>Verdict:</b> {verdict} ({confidence:.0f}%)\n"
            f"<b>VIX:</b> {vix:.1f} | <b>IV Skew:</b> {iv_skew:.2f}\n\n"
            f"<i>Price leads OI \u2014 no verdict alignment needed.</i>\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )

    @staticmethod
    def _format_exit_alert(trade, exit_premium, reason, pnl) -> str:
        result_emoji = "\u2705" if pnl > 0 else "\u274c"
        reason_text = {
            "TARGET": f"Target Hit (+{_cfg.TARGET_PCT:.0f}%)",
            "SL": f"Stop Loss (-{_cfg.SL_PCT:.0f}%)",
            "EOD": "End of Day",
        }.get(reason, reason)
        return (
            f"<b>{result_emoji} PA {'WON' if pnl > 0 else 'LOST'}</b>\n\n"
            f"<b>Strike:</b> <code>{trade['strike']} {trade['option_type']}</code>\n"
            f"<b>Entry:</b> <code>Rs {trade['entry_premium']:.2f}</code>\n"
            f"<b>Exit:</b> <code>Rs {exit_premium:.2f}</code>\n"
            f"<b>P&L:</b> <code>{pnl:+.2f}%</code>\n"
            f"<b>Reason:</b> {reason_text}\n\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )
