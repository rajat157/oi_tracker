"""
Options Selling Tracker - Config B strategy adapted for selling

Signal Logic (same as buying Config B):
- Time Window: 11:00 - 14:00 IST
- Verdict: "Slightly Bullish" or "Slightly Bearish" only
- Confidence: >= 65%
- One trade per day (first valid signal)

Selling Direction:
- Slightly Bullish -> SELL OTM PUT (puts decay as market rises)
- Slightly Bearish -> SELL OTM CALL (calls decay as market falls)

Strike Selection: 1 strike OTM from ATM
SL: 25% premium rise (loss for seller)
Target: 25% premium drop (profit for seller)
EOD Exit: 15:20 if no SL/Target hit
"""

from datetime import datetime, time, timedelta
from typing import Optional, Dict, List
from database import get_connection
from logger import get_logger

log = get_logger("selling_tracker")

# Strategy constants
SELL_TIME_START = time(11, 0)
SELL_TIME_END = time(14, 0)
SELL_MIN_CONFIDENCE = 65.0
SELL_SL_PCT = 25.0      # Premium rises 25% = loss
SELL_TARGET_PCT = 25.0   # Premium drops 25% = profit
SELL_OTM_OFFSET = 1      # 1 strike OTM
SELL_MIN_PREMIUM = 5.0   # Min premium to sell (avoid illiquid)
FORCE_CLOSE_TIME = time(15, 20)
NIFTY_STEP = 50


def init_selling_tables():
    """Create selling trade tables if they don't exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sell_trade_setups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME NOT NULL,
                direction TEXT NOT NULL,
                strike INTEGER NOT NULL,
                option_type TEXT NOT NULL,
                entry_premium REAL NOT NULL,
                sl_premium REAL NOT NULL,
                target_premium REAL NOT NULL,
                spot_at_creation REAL NOT NULL,
                verdict_at_creation TEXT NOT NULL,
                signal_confidence REAL,
                iv_at_creation REAL,
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
        log.info("Selling tables initialized")


def get_otm_strike(spot_price: float, direction: str) -> int:
    """Get OTM strike for selling."""
    atm = round(spot_price / NIFTY_STEP) * NIFTY_STEP
    if direction == "SELL_PUT":
        return atm - (NIFTY_STEP * SELL_OTM_OFFSET)
    else:  # SELL_CALL
        return atm + (NIFTY_STEP * SELL_OTM_OFFSET)


def get_active_sell_setup() -> Optional[Dict]:
    """Get currently active selling trade setup."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM sell_trade_setups
            WHERE status = 'ACTIVE'
            ORDER BY created_at DESC LIMIT 1
        """)
        row = cursor.fetchone()
        return dict(row) if row else None


def get_todays_sell_trades() -> List[Dict]:
    """Get all selling trades created today."""
    today = datetime.now().strftime('%Y-%m-%d')
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM sell_trade_setups
            WHERE DATE(created_at) = ?
            ORDER BY created_at
        """, (today,))
        return [dict(r) for r in cursor.fetchall()]


def save_sell_setup(created_at, direction, strike, option_type, entry_premium,
                    sl_premium, target_premium, spot, verdict, confidence, iv) -> int:
    """Save a new selling trade setup."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sell_trade_setups
            (created_at, direction, strike, option_type, entry_premium, sl_premium,
             target_premium, spot_at_creation, verdict_at_creation, signal_confidence,
             iv_at_creation, status, max_premium_reached, min_premium_reached)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
        """, (created_at, direction, strike, option_type, entry_premium,
              sl_premium, target_premium, spot, verdict, confidence, iv,
              entry_premium, entry_premium))
        conn.commit()
        return cursor.lastrowid


def update_sell_setup(setup_id: int, **kwargs):
    """Update a selling trade setup."""
    if not kwargs:
        return
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [setup_id]
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE sell_trade_setups SET {set_clause} WHERE id = ?", values)
        conn.commit()


class SellingTracker:
    """Manages options selling trade lifecycle."""

    def __init__(self):
        init_selling_tables()

    def should_create_sell_setup(self, analysis: dict) -> bool:
        """Check if conditions are met for a new selling trade."""
        # One trade per day
        if get_todays_sell_trades():
            return False

        # Active setup exists
        if get_active_sell_setup():
            return False

        # Time window
        now = datetime.now().time()
        if now < SELL_TIME_START or now > SELL_TIME_END:
            return False

        # Verdict check
        verdict = analysis.get("verdict", "")
        if "Slightly" not in verdict:
            return False

        # Confidence check
        confidence = analysis.get("signal_confidence", 0)
        if confidence < SELL_MIN_CONFIDENCE:
            return False

        return True

    def create_sell_setup(self, analysis: dict, strikes_data: dict) -> Optional[int]:
        """
        Create a new selling trade setup.

        Args:
            analysis: Current OI analysis dict
            strikes_data: Current option chain data with prices

        Returns:
            Setup ID if created, None otherwise
        """
        verdict = analysis.get("verdict", "")
        spot = analysis.get("spot_price", 0)
        confidence = analysis.get("signal_confidence", 0)

        # Determine direction
        if "Bullish" in verdict:
            direction = "SELL_PUT"
            option_type = "PE"
        else:
            direction = "SELL_CALL"
            option_type = "CE"

        strike = get_otm_strike(spot, direction)

        # Get current premium from strikes data
        strike_data = strikes_data.get(strike, {})
        if option_type == "PE":
            entry_premium = strike_data.get("pe_ltp", 0)
            iv = strike_data.get("pe_iv", 0)
        else:
            entry_premium = strike_data.get("ce_ltp", 0)
            iv = strike_data.get("ce_iv", 0)

        if not entry_premium or entry_premium < SELL_MIN_PREMIUM:
            log.warning("Sell setup skipped: premium too low",
                       strike=strike, premium=entry_premium)
            return None

        # Calculate SL and target for seller
        sl_premium = round(entry_premium * (1 + SELL_SL_PCT / 100), 2)
        target_premium = round(entry_premium * (1 - SELL_TARGET_PCT / 100), 2)

        now = datetime.now()
        setup_id = save_sell_setup(
            created_at=now,
            direction=direction,
            strike=strike,
            option_type=option_type,
            entry_premium=entry_premium,
            sl_premium=sl_premium,
            target_premium=target_premium,
            spot=spot,
            verdict=verdict,
            confidence=confidence,
            iv=iv
        )

        log.info("Created SELL setup",
                 setup_id=setup_id, direction=direction,
                 strike=f"{strike} {option_type}",
                 entry=entry_premium, sl=sl_premium, target=target_premium)

        # Send Telegram alert
        self._send_sell_alert(direction, strike, option_type, entry_premium,
                              sl_premium, target_premium, verdict, confidence, spot)

        return setup_id

    def check_and_update_sell_setup(self, strikes_data: dict) -> Optional[Dict]:
        """
        Check and update active selling trade setup.

        For sellers:
        - Premium RISES above SL = LOSS (buy back at higher price)
        - Premium DROPS below target = WIN (buy back at lower price)
        - EOD at 15:20 = close at market

        Returns:
            Dict with update info if status changed
        """
        setup = get_active_sell_setup()
        if not setup:
            return None

        strike = setup["strike"]
        option_type = setup["option_type"]

        strike_data = strikes_data.get(strike, {})
        current_premium = strike_data.get(
            "ce_ltp" if option_type == "CE" else "pe_ltp", 0
        )

        if current_premium <= 0:
            return None

        now = datetime.now()

        # Track extremes
        max_prem = max(setup.get("max_premium_reached") or setup["entry_premium"], current_premium)
        min_prem = min(setup.get("min_premium_reached") or setup["entry_premium"], current_premium)

        update_sell_setup(setup["id"],
                         last_checked_at=now,
                         last_premium=current_premium,
                         max_premium_reached=max_prem,
                         min_premium_reached=min_prem)

        # Check SL (premium rises = loss for seller)
        if current_premium >= setup["sl_premium"]:
            pnl = ((setup["entry_premium"] - current_premium) / setup["entry_premium"]) * 100
            update_sell_setup(setup["id"],
                             status="LOST", resolved_at=now,
                             exit_premium=current_premium, exit_reason="SL",
                             profit_loss_pct=pnl)
            log.info("SELL trade LOST (SL hit)", pnl=f"{pnl:.2f}%",
                     entry=setup["entry_premium"], exit=current_premium)
            self._send_exit_alert(setup, current_premium, "SL", pnl)
            return {"action": "LOST", "pnl": pnl, "reason": "SL"}

        # Check target (premium drops = profit for seller)
        if current_premium <= setup["target_premium"]:
            pnl = ((setup["entry_premium"] - current_premium) / setup["entry_premium"]) * 100
            update_sell_setup(setup["id"],
                             status="WON", resolved_at=now,
                             exit_premium=current_premium, exit_reason="TARGET",
                             profit_loss_pct=pnl)
            log.info("SELL trade WON (target hit)", pnl=f"{pnl:.2f}%",
                     entry=setup["entry_premium"], exit=current_premium)
            self._send_exit_alert(setup, current_premium, "TARGET", pnl)
            return {"action": "WON", "pnl": pnl, "reason": "TARGET"}

        # EOD exit
        if now.time() >= FORCE_CLOSE_TIME:
            pnl = ((setup["entry_premium"] - current_premium) / setup["entry_premium"]) * 100
            status = "WON" if pnl > 0 else "LOST"
            update_sell_setup(setup["id"],
                             status=status, resolved_at=now,
                             exit_premium=current_premium, exit_reason="EOD",
                             profit_loss_pct=pnl)
            log.info(f"SELL trade {status} (EOD)", pnl=f"{pnl:.2f}%")
            self._send_exit_alert(setup, current_premium, "EOD", pnl)
            return {"action": status, "pnl": pnl, "reason": "EOD"}

        return None

    def _send_sell_alert(self, direction, strike, option_type, entry, sl, target,
                         verdict, confidence, spot):
        """Send Telegram alert for new selling setup."""
        try:
            from alerts import send_telegram
            risk_pct = SELL_SL_PCT
            target_pct = SELL_TARGET_PCT

            dir_text = "SELL PUT" if direction == "SELL_PUT" else "SELL CALL"
            emoji = "🔴" if "CALL" in direction else "🟢"

            message = (
                f"<b>{emoji} SELL SETUP</b>\n\n"
                f"<b>Direction:</b> <code>{dir_text}</code>\n"
                f"<b>Strike:</b> <code>{strike} {option_type}</code>\n"
                f"<b>Spot:</b> <code>{spot:.2f}</code>\n"
                f"<b>Premium Collected:</b> <code>Rs {entry:.2f}</code>\n"
                f"<b>SL (buyback):</b> <code>Rs {sl:.2f}</code> (+{risk_pct:.0f}% rise)\n"
                f"<b>Target (buyback):</b> <code>Rs {target:.2f}</code> (-{target_pct:.0f}% drop)\n\n"
                f"<b>Verdict:</b> {verdict}\n"
                f"<b>Confidence:</b> {confidence:.0f}%\n\n"
                f"<i>Seller profits when premium decays</i>\n"
                f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
            )
            send_telegram(message)
        except Exception as e:
            log.error("Failed to send sell alert", error=str(e))

    def _send_exit_alert(self, setup, exit_premium, reason, pnl):
        """Send Telegram alert when selling trade exits."""
        try:
            from alerts import send_telegram
            emoji = "✅" if pnl > 0 else "❌"
            dir_text = "SELL PUT" if setup["direction"] == "SELL_PUT" else "SELL CALL"

            message = (
                f"<b>{emoji} SELL TRADE {'WON' if pnl > 0 else 'LOST'}</b>\n\n"
                f"<b>Direction:</b> <code>{dir_text}</code>\n"
                f"<b>Strike:</b> <code>{setup['strike']} {setup['option_type']}</code>\n"
                f"<b>Entry Premium:</b> <code>Rs {setup['entry_premium']:.2f}</code>\n"
                f"<b>Exit Premium:</b> <code>Rs {exit_premium:.2f}</code>\n"
                f"<b>P&L:</b> <code>{pnl:+.2f}%</code>\n"
                f"<b>Exit Reason:</b> {reason}\n\n"
                f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
            )
            send_telegram(message)
        except Exception as e:
            log.error("Failed to send sell exit alert", error=str(e))

    def get_sell_stats(self) -> Dict:
        """Get selling trade statistics."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) as losses,
                       AVG(CASE WHEN profit_loss_pct > 0 THEN profit_loss_pct END) as avg_win,
                       AVG(CASE WHEN profit_loss_pct <= 0 THEN profit_loss_pct END) as avg_loss,
                       SUM(profit_loss_pct) as total_pnl
                FROM sell_trade_setups
                WHERE status IN ('WON', 'LOST')
            """)
            row = cursor.fetchone()
            if not row:
                return {}
            return dict(row)
