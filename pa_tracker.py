"""
Price Action (PA) Tracker — ATM Premium Price Action buying strategy.

Fixes ATM strike at market open, tracks CE and PE premiums on 3-minute candles,
and enters long when 3 consecutive higher closes (CHC-3) are detected.

Backtested: 72.7% WR | +237.4% P&L | -26.1% Max DD (22 days)
Forward test: 81.8% WR | +158.5% P&L | -19.7% Max DD (11 days)

Config:
- Entry signal: CHC(3) — 3 consecutive higher closes on CE or PE
- Strike: ATM at market open (fixed for the day)
- SL: -15% | Target: +15% (1:1 RR)
- Time window: 9:30–14:00 IST
- One trade per day max
- EOD exit: 15:20
- Filter 1: IV Skew — skip CE if iv_skew > 1.0, skip PE if iv_skew < -1.0
- Filter 2: Choppy — skip if spot range < 0.15% over last 10 candles
"""

import os
from datetime import datetime, time
from typing import Optional, Dict, List
from database import get_connection
from logger import get_logger

log = get_logger("pa_tracker")

# Order placement config (from .env)
PA_PLACE_ORDER = os.getenv("PA_PLACE_ORDER", "false").lower() == "true"
PA_LOTS = int(os.getenv("PA_LOTS", "1"))
NIFTY_LOT_SIZE = int(os.getenv("NIFTY_LOT_SIZE", "65"))

# Strategy constants
PA_TIME_START = time(9, 30)
PA_TIME_END = time(14, 0)
PA_SL_PCT = 15.0
PA_TARGET_PCT = 15.0   # 1:1 RR
PA_MIN_PREMIUM = 5.0
FORCE_CLOSE_TIME = time(15, 20)
NIFTY_STEP = 50
CHC_LOOKBACK = 3       # 3 consecutive higher closes
CHOPPY_LOOKBACK = 10   # 10 candles for choppy detection
CHOPPY_THRESHOLD = 0.15  # 0.15% spot range = choppy
PA_VIX_WARN_THRESHOLD = 18.0  # Paper trade only when VIX above this

STRATEGY_NAME = "Price Action"


def init_pa_tables():
    """Create PA trade table if it doesn't exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pa_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME NOT NULL,
                direction TEXT NOT NULL,
                strike INTEGER NOT NULL,
                option_type TEXT NOT NULL,
                entry_premium REAL NOT NULL,
                sl_premium REAL NOT NULL,
                target_premium REAL NOT NULL,
                spot_at_creation REAL NOT NULL,
                verdict_at_creation TEXT,
                signal_confidence REAL,
                iv_skew_at_creation REAL,
                vix_at_creation REAL,
                chc_strength REAL,
                status TEXT DEFAULT 'ACTIVE',
                resolved_at DATETIME,
                exit_premium REAL,
                exit_reason TEXT,
                profit_loss_pct REAL,
                max_premium_reached REAL,
                min_premium_reached REAL,
                last_checked_at DATETIME,
                last_premium REAL
            )
        """)
        conn.commit()
        log.info("PA tables initialized")


def get_active_pa() -> Optional[Dict]:
    """Get currently active PA trade."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM pa_trades
            WHERE status = 'ACTIVE'
            ORDER BY created_at DESC LIMIT 1
        """)
        row = cursor.fetchone()
        return dict(row) if row else None


def get_todays_pa_trades() -> List[Dict]:
    """Get all PA trades created today."""
    today = datetime.now().strftime('%Y-%m-%d')
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM pa_trades
            WHERE DATE(created_at) = ?
            ORDER BY created_at
        """, (today,))
        return [dict(r) for r in cursor.fetchall()]


def save_pa_trade(created_at, direction, strike, option_type, entry_premium,
                  sl_premium, target_premium, spot, verdict, confidence,
                  iv_skew, vix, chc_strength) -> int:
    """Save a new PA trade."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pa_trades
            (created_at, direction, strike, option_type, entry_premium,
             sl_premium, target_premium, spot_at_creation, verdict_at_creation,
             signal_confidence, iv_skew_at_creation, vix_at_creation,
             chc_strength, status, max_premium_reached, min_premium_reached)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
        """, (created_at, direction, strike, option_type, entry_premium,
              sl_premium, target_premium, spot, verdict, confidence, iv_skew,
              vix, chc_strength, entry_premium, entry_premium))
        conn.commit()
        return cursor.lastrowid


def update_pa_trade(trade_id: int, **kwargs):
    """Update a PA trade."""
    if not kwargs:
        return
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [trade_id]
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE pa_trades SET {set_clause} WHERE id = ?", values)
        conn.commit()


class PulseRiderTracker:
    """Manages ATM premium price action buying strategy."""

    def __init__(self):
        init_pa_tables()
        self.atm_strike = None
        self.premium_history: List[Dict] = []
        self._current_date = None

    def reset_day(self):
        """Reset state for a new trading day."""
        self.atm_strike = None
        self.premium_history = []
        log.info("PA tracker reset for new day")

    def _lock_atm_strike(self, spot_price: float):
        """Lock ATM strike at market open (nearest 50)."""
        self.atm_strike = round(spot_price / NIFTY_STEP) * NIFTY_STEP
        log.info("PA ATM strike locked", strike=self.atm_strike, spot=f"{spot_price:.2f}")

    def _record_premium(self, strikes_data: dict, timestamp: datetime, spot_price: float):
        """Record ATM CE and PE premiums for the current candle."""
        if not self.atm_strike:
            return

        strike_data = strikes_data.get(self.atm_strike, {})
        ce_ltp = strike_data.get("ce_ltp", 0)
        pe_ltp = strike_data.get("pe_ltp", 0)

        if ce_ltp <= 0 or pe_ltp <= 0:
            return

        self.premium_history.append({
            "ts": timestamp,
            "ce_ltp": ce_ltp,
            "pe_ltp": pe_ltp,
            "spot": spot_price,
        })

    def _detect_momentum(self) -> Optional[tuple]:
        """
        Detect CHC(3) — 3 consecutive higher closes on CE or PE.
        Returns ('CE', strength_pct) or ('PE', strength_pct) or None.
        """
        n = len(self.premium_history)
        if n < CHC_LOOKBACK + 1:
            return None

        # Check last CHC_LOOKBACK candles for consecutive higher closes
        recent = self.premium_history[-(CHC_LOOKBACK + 1):]

        ce_rising = all(
            recent[i]["ce_ltp"] > recent[i - 1]["ce_ltp"]
            for i in range(1, CHC_LOOKBACK + 1)
        )
        pe_rising = all(
            recent[i]["pe_ltp"] > recent[i - 1]["pe_ltp"]
            for i in range(1, CHC_LOOKBACK + 1)
        )

        # Calculate strength (% move over CHC window)
        ce_start = recent[0]["ce_ltp"]
        pe_start = recent[0]["pe_ltp"]
        ce_pct = ((recent[-1]["ce_ltp"] - ce_start) / ce_start) if ce_start > 0 else 0
        pe_pct = ((recent[-1]["pe_ltp"] - pe_start) / pe_start) if pe_start > 0 else 0

        if ce_rising and not pe_rising:
            return ("CE", ce_pct)
        elif pe_rising and not ce_rising:
            return ("PE", pe_pct)
        elif ce_rising and pe_rising:
            # Both rising — pick the stronger side
            return ("CE", ce_pct) if ce_pct > pe_pct else ("PE", pe_pct)

        return None

    def _is_iv_skew_ok(self, side: str, iv_skew: float) -> bool:
        """IV Skew filter: skip CE if iv_skew > 1.0, skip PE if iv_skew < -1.0."""
        if side == "CE" and iv_skew > 1.0:
            return False
        if side == "PE" and iv_skew < -1.0:
            return False
        return True

    def _is_choppy(self) -> bool:
        """Check if market is choppy (spot range < 0.15% over last 10 candles)."""
        if len(self.premium_history) < CHOPPY_LOOKBACK + 1:
            return False

        recent_spots = [
            c["spot"] for c in self.premium_history[-(CHOPPY_LOOKBACK + 1):]
        ]
        avg_spot = sum(recent_spots) / len(recent_spots)
        if avg_spot <= 0:
            return False

        spot_range_pct = ((max(recent_spots) - min(recent_spots)) / avg_spot) * 100
        return spot_range_pct < CHOPPY_THRESHOLD

    def should_create_trade(self, analysis: dict, strikes_data: dict) -> Optional[str]:
        """
        Main entry check — called every 3 minutes.
        Returns side ('CE' or 'PE') if all conditions pass, else None.
        """
        now = datetime.now()

        # Auto-reset on new day
        today = now.date()
        if self._current_date != today:
            self.reset_day()
            self._current_date = today

        # One trade per day
        if get_todays_pa_trades():
            return None

        # No active trade
        if get_active_pa():
            return None
        if now.time() < PA_TIME_START or now.time() > PA_TIME_END:
            return None

        spot_price = analysis.get("spot_price", 0)
        if spot_price <= 0:
            return None

        # Lock ATM if not locked (first candle of the day)
        if not self.atm_strike:
            self._lock_atm_strike(spot_price)

        # Record premium
        self._record_premium(strikes_data, now, spot_price)

        # Need enough candles for CHC detection
        if len(self.premium_history) < CHC_LOOKBACK + 1:
            return None

        # Detect CHC(3)
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

        log.info("PA CHC(3) detected", side=side, strength=f"{strength:.2%}")
        return side

    def create_trade(self, side: str, analysis: dict, strikes_data: dict) -> Optional[int]:
        """Create a new PA trade."""
        spot = analysis.get("spot_price", 0)
        verdict = analysis.get("verdict", "")
        confidence = analysis.get("signal_confidence", 0)
        iv_skew = analysis.get("iv_skew", 0) or 0
        vix = analysis.get("vix", 0) or 0

        option_type = side  # "CE" or "PE"
        direction = "BUY_CALL" if side == "CE" else "BUY_PUT"

        # Get ATM premium for the side
        strike_data = strikes_data.get(self.atm_strike, {})
        entry_premium = strike_data.get(
            "ce_ltp" if side == "CE" else "pe_ltp", 0
        )

        if not entry_premium or entry_premium < PA_MIN_PREMIUM:
            log.warning("PA skipped: premium too low",
                       strike=self.atm_strike, premium=entry_premium)
            return None

        sl_premium = round(entry_premium * (1 - PA_SL_PCT / 100), 2)
        target_premium = round(entry_premium * (1 + PA_TARGET_PCT / 100), 2)

        # Get CHC strength from last detection
        signal = self._detect_momentum()
        chc_strength = signal[1] if signal else 0

        now = datetime.now()
        trade_id = save_pa_trade(
            created_at=now,
            direction=direction,
            strike=self.atm_strike,
            option_type=option_type,
            entry_premium=entry_premium,
            sl_premium=sl_premium,
            target_premium=target_premium,
            spot=spot,
            verdict=verdict,
            confidence=confidence,
            iv_skew=iv_skew,
            vix=vix,
            chc_strength=chc_strength,
        )

        log.info("Created PA trade",
                 trade_id=trade_id, direction=direction,
                 strike=f"{self.atm_strike} {option_type}",
                 entry=entry_premium, sl=sl_premium, target=target_premium)

        self._send_entry_alert(direction, self.atm_strike, option_type,
                               entry_premium, sl_premium, target_premium,
                               spot, verdict, confidence, iv_skew, vix, chc_strength)

        # Auto-place order on Kite if enabled (skip on high VIX — paper trade)
        is_paper = vix > PA_VIX_WARN_THRESHOLD
        if PA_PLACE_ORDER and not is_paper:
            self._place_kite_order(
                trade_id, self.atm_strike, option_type,
                entry_premium, sl_premium, target_premium,
                analysis.get("expiry_date", "")
            )
        elif is_paper:
            log.info("PA paper trade: VIX too high, skipping Kite order", vix=f"{vix:.1f}")

        return trade_id

    def check_and_update_trade(self, strikes_data: dict, timestamp: datetime) -> Optional[Dict]:
        """Check active PA trade for SL/Target/EOD."""
        trade = get_active_pa()
        if not trade:
            return None

        strike = trade["strike"]
        option_type = trade["option_type"]

        strike_data = strikes_data.get(strike, {})
        current = strike_data.get(
            "ce_ltp" if option_type == "CE" else "pe_ltp", 0
        )

        if current <= 0:
            return None

        now = datetime.now()
        entry = trade["entry_premium"]

        # Track extremes
        max_p = max(trade.get("max_premium_reached") or entry, current)
        min_p = min(trade.get("min_premium_reached") or entry, current)

        update_pa_trade(trade["id"],
                       last_checked_at=now,
                       last_premium=current,
                       max_premium_reached=max_p,
                       min_premium_reached=min_p)

        # Check SL (premium drops = loss for buyer)
        if current <= trade["sl_premium"]:
            pnl = ((current - entry) / entry) * 100
            update_pa_trade(trade["id"],
                           status="LOST", resolved_at=now,
                           exit_premium=current, exit_reason="SL",
                           profit_loss_pct=pnl)
            log.info("PA LOST (SL)", pnl=f"{pnl:.2f}%", entry=entry, exit=current)
            self._send_exit_alert(trade, current, "SL", pnl)
            return {"action": "LOST", "pnl": pnl, "reason": "SL"}

        # Check Target (premium rises = win for buyer)
        if current >= trade["target_premium"]:
            pnl = ((current - entry) / entry) * 100
            update_pa_trade(trade["id"],
                           status="WON", resolved_at=now,
                           exit_premium=current, exit_reason="TARGET",
                           profit_loss_pct=pnl)
            log.info("PA WON (TARGET)", pnl=f"{pnl:.2f}%", entry=entry, exit=current)
            self._send_exit_alert(trade, current, "TARGET", pnl)
            return {"action": "WON", "pnl": pnl, "reason": "TARGET"}

        # EOD exit
        if now.time() >= FORCE_CLOSE_TIME:
            pnl = ((current - entry) / entry) * 100
            status = "WON" if pnl > 0 else "LOST"
            update_pa_trade(trade["id"],
                           status=status, resolved_at=now,
                           exit_premium=current, exit_reason="EOD",
                           profit_loss_pct=pnl)
            log.info(f"PA {status} (EOD)", pnl=f"{pnl:.2f}%")
            self._send_exit_alert(trade, current, "EOD", pnl)
            return {"action": status, "pnl": pnl, "reason": "EOD"}

        return None

    def _send_entry_alert(self, direction, strike, option_type, entry, sl, target,
                          spot, verdict, confidence, iv_skew, vix, chc_strength):
        """Send Telegram alert for new PA trade."""
        try:
            from alerts import send_telegram

            side_emoji = "\U0001f7e2" if option_type == "CE" else "\U0001f534"
            vix_warning = ""
            if vix > PA_VIX_WARN_THRESHOLD:
                vix_warning = (
                    f"\n\u26a0\ufe0f <b>HIGH VIX ({vix:.1f}) — PAPER TRADE ONLY</b>\n"
                    f"<i>Kite order NOT placed. Tracking for data collection.</i>\n"
                )

            message = (
                f"<b>{side_emoji} PRICE ACTION: {direction}</b>\n"
                f"{vix_warning}\n"
                f"<b>Signal:</b> CHC(3) on {option_type} ({chc_strength:.1%} move)\n"
                f"<b>Strike:</b> <code>{strike} {option_type}</code>\n"
                f"<b>Spot:</b> <code>{spot:.2f}</code>\n"
                f"<b>Entry:</b> <code>Rs {entry:.2f}</code>\n"
                f"<b>SL:</b> <code>Rs {sl:.2f}</code> (-{PA_SL_PCT:.0f}%)\n"
                f"<b>Target:</b> <code>Rs {target:.2f}</code> (+{PA_TARGET_PCT:.0f}%)\n"
                f"<b>RR:</b> <code>1:1</code>\n\n"
                f"<b>Verdict:</b> {verdict} ({confidence:.0f}%)\n"
                f"<b>VIX:</b> {vix:.1f} | <b>IV Skew:</b> {iv_skew:.2f}\n\n"
                f"<i>Price leads OI — no verdict alignment needed.</i>\n"
                f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
            )
            send_telegram(message)
        except Exception as e:
            log.error("Failed to send PA entry alert", error=str(e))

    def _send_exit_alert(self, trade, exit_premium, reason, pnl):
        """Send Telegram alert when PA trade exits."""
        try:
            from alerts import send_telegram

            result_emoji = "\u2705" if pnl > 0 else "\u274c"
            reason_text = {
                "TARGET": f"Target Hit (+{PA_TARGET_PCT:.0f}%)",
                "SL": f"Stop Loss (-{PA_SL_PCT:.0f}%)",
                "EOD": "End of Day",
            }.get(reason, reason)

            message = (
                f"<b>{result_emoji} PA {'WON' if pnl > 0 else 'LOST'}</b>\n\n"
                f"<b>Strike:</b> <code>{trade['strike']} {trade['option_type']}</code>\n"
                f"<b>Entry:</b> <code>Rs {trade['entry_premium']:.2f}</code>\n"
                f"<b>Exit:</b> <code>Rs {exit_premium:.2f}</code>\n"
                f"<b>P&L:</b> <code>{pnl:+.2f}%</code>\n"
                f"<b>Reason:</b> {reason_text}\n\n"
                f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
            )
            send_telegram(message)
        except Exception as e:
            log.error("Failed to send PA exit alert", error=str(e))

    def _place_kite_order(self, trade_id: int, strike: int, option_type: str,
                          entry_premium: float, sl_premium: float,
                          target_premium: float, expiry_date: str):
        """Place LIMIT BUY + GTT OCO on Kite for a PA trade."""
        try:
            from kite_broker import is_authenticated, place_order, place_gtt_oco, round_to_tick
            from alerts import _get_kite_trading_symbol, send_telegram

            if not is_authenticated():
                log.warning("PA order skipped: Kite not authenticated")
                return

            trading_symbol = _get_kite_trading_symbol(strike, option_type, expiry_date)
            quantity = PA_LOTS * NIFTY_LOT_SIZE

            # Round prices to tick size (0.5)
            entry = round_to_tick(entry_premium, "nearest")
            sl = round_to_tick(sl_premium, "down")
            target = round_to_tick(target_premium, "up")

            log.info("PA auto-placing order", symbol=trading_symbol,
                     entry=entry, sl=sl, target=target, qty=quantity, lots=PA_LOTS)

            # 1. Place LIMIT BUY order
            order_result = place_order(
                trading_symbol=trading_symbol,
                transaction_type="BUY",
                quantity=quantity,
                price=entry,
                order_type="LIMIT",
                product="NRML"
            )

            if order_result.get("status") != "success":
                log.error("PA buy order failed", result=order_result)
                send_telegram(
                    f"<b>\u274c PA Order Failed</b>\n"
                    f"Symbol: <code>{trading_symbol}</code>\n"
                    f"Error: {order_result.get('message', 'Unknown')}"
                )
                return

            order_id = order_result["data"]["order_id"]

            # 2. Place GTT OCO (SL + Target)
            gtt_result = place_gtt_oco(
                trading_symbol=trading_symbol,
                entry_price=entry,
                sl_price=sl,
                target_price=target,
                quantity=quantity,
                product="NRML"
            )

            trigger_id = None
            if gtt_result.get("status") == "success":
                trigger_id = gtt_result["data"]["trigger_id"]
            else:
                log.error("PA GTT failed — order is live without SL!", result=gtt_result)

            # Send confirmation alert
            order_msg = (
                f"<b>\u2705 PA Order Placed on Kite!</b>\n\n"
                f"<b>Symbol:</b> <code>{trading_symbol}</code>\n"
                f"<b>Order:</b> BUY LIMIT @ Rs {entry:.1f} | Qty {quantity} ({PA_LOTS} lot{'s' if PA_LOTS > 1 else ''})\n"
                f"<b>GTT SL:</b> Rs {sl:.1f}\n"
                f"<b>GTT Target:</b> Rs {target:.1f}\n"
                f"<b>Order ID:</b> <code>{order_id}</code>\n"
                f"<b>GTT ID:</b> <code>{trigger_id or 'FAILED'}</code>"
            )
            send_telegram(order_msg)
            log.info("PA order placed", order_id=order_id,
                     trigger_id=trigger_id, lots=PA_LOTS, qty=quantity)

        except Exception as e:
            log.error("PA auto order placement error", error=str(e))
            try:
                from alerts import send_telegram
                send_telegram(f"<b>\u274c PA Order Error</b>\n<code>{str(e)}</code>")
            except Exception:
                pass

    def get_pa_stats(self) -> Dict:
        """Get PA trade statistics."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) as losses,
                       SUM(profit_loss_pct) as total_pnl,
                       AVG(CASE WHEN status='WON' THEN profit_loss_pct END) as avg_win,
                       AVG(CASE WHEN status='LOST' THEN profit_loss_pct END) as avg_loss
                FROM pa_trades
                WHERE status IN ('WON', 'LOST')
            """)
            overall = dict(cursor.fetchone())

            # Per-direction stats
            cursor.execute("""
                SELECT direction,
                       COUNT(*) as total,
                       SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) as wins,
                       SUM(profit_loss_pct) as total_pnl
                FROM pa_trades
                WHERE status IN ('WON', 'LOST')
                GROUP BY direction
            """)
            by_direction = {}
            for row in cursor.fetchall():
                r = dict(row)
                by_direction[r["direction"]] = r

            return {"overall": overall, "by_direction": by_direction}
