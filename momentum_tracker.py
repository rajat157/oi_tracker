"""
Momentum Tracker — Trend-following 1:2 RR buying on high-conviction days.

Unlike Dessert strategies (contrarian), this fires when OI, verdict, and price
ALL agree — the "triple alignment" setup.

Supports both directions:
- BUY_PUT when Bears Winning/Strongly Winning + CONFIRMED
- BUY_CALL when Bulls Winning/Strongly Winning + CONFIRMED

Config:
- Window: 12:00–14:00 (trend needs time to establish)
- SL: 25%, Target: 50% (1:2 RR)
- Strike: ATM
- One trade per day max
- Requires: Winning/Strongly Winning + CONFIRMED + conf >= 85%
"""

import json
from datetime import datetime, time
from typing import Optional, Dict, List
from database import get_connection
from logger import get_logger

log = get_logger("momentum_tracker")

# Strategy constants
MOMENTUM_TIME_START = time(12, 0)
MOMENTUM_TIME_END = time(14, 0)
MOMENTUM_SL_PCT = 25.0
MOMENTUM_TARGET_PCT = 50.0  # 1:2 RR
MOMENTUM_MIN_PREMIUM = 5.0
MOMENTUM_MIN_CONFIDENCE = 85.0
FORCE_CLOSE_TIME = time(15, 20)
NIFTY_STEP = 50

STRATEGY_NAME = "Momentum"

# Valid verdicts for entry
BEARISH_VERDICTS = ("Bears Winning", "Bears Strongly Winning")
BULLISH_VERDICTS = ("Bulls Winning", "Bulls Strongly Winning")


def init_momentum_tables():
    """Create momentum trade table if it doesn't exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS momentum_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME NOT NULL,
                strategy_name TEXT NOT NULL DEFAULT 'Momentum',
                direction TEXT NOT NULL,
                strike INTEGER NOT NULL,
                option_type TEXT NOT NULL,
                entry_premium REAL NOT NULL,
                sl_premium REAL NOT NULL,
                target_premium REAL NOT NULL,
                spot_at_creation REAL NOT NULL,
                verdict_at_creation TEXT NOT NULL,
                signal_confidence REAL,
                iv_skew_at_creation REAL,
                vix_at_creation REAL,
                combined_score REAL,
                confirmation_status TEXT,
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
        log.info("Momentum tables initialized")


def get_active_momentum() -> Optional[Dict]:
    """Get currently active momentum trade."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM momentum_trades
            WHERE status = 'ACTIVE'
            ORDER BY created_at DESC LIMIT 1
        """)
        row = cursor.fetchone()
        return dict(row) if row else None


def get_todays_momentum_trades() -> List[Dict]:
    """Get all momentum trades created today."""
    today = datetime.now().strftime('%Y-%m-%d')
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM momentum_trades
            WHERE DATE(created_at) = ?
            ORDER BY created_at
        """, (today,))
        return [dict(r) for r in cursor.fetchall()]


def save_momentum_trade(direction, strike, option_type, entry_premium,
                        sl_premium, target_premium, spot, verdict,
                        confidence, iv_skew, vix, combined_score,
                        confirmation_status) -> int:
    """Save a new momentum trade."""
    now = datetime.now()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO momentum_trades
            (created_at, strategy_name, direction, strike, option_type,
             entry_premium, sl_premium, target_premium, spot_at_creation,
             verdict_at_creation, signal_confidence, iv_skew_at_creation,
             vix_at_creation, combined_score, confirmation_status,
             status, max_premium_reached, min_premium_reached)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
        """, (now, STRATEGY_NAME, direction, strike, option_type,
              entry_premium, sl_premium, target_premium, spot, verdict,
              confidence, iv_skew, vix, combined_score, confirmation_status,
              entry_premium, entry_premium))
        conn.commit()
        return cursor.lastrowid


def update_momentum_trade(trade_id: int, **kwargs):
    """Update a momentum trade."""
    if not kwargs:
        return
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [trade_id]
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE momentum_trades SET {set_clause} WHERE id = ?", values)
        conn.commit()


class MomentumTracker:
    """Manages trend-following 1:2 RR buying strategy."""

    def __init__(self):
        init_momentum_tables()

    def should_create_momentum(self, analysis: dict) -> Optional[str]:
        """
        Check if momentum strategy should trigger.
        Returns direction ('BUY_PUT' or 'BUY_CALL') or None.
        """
        # One trade per day max
        if get_todays_momentum_trades():
            return None

        # No active trade
        if get_active_momentum():
            return None

        # Time window: 12:00–14:00
        now = datetime.now().time()
        if now < MOMENTUM_TIME_START or now > MOMENTUM_TIME_END:
            return None

        verdict = analysis.get("verdict", "")
        confidence = analysis.get("signal_confidence", 0) or 0

        # Confidence filter
        if confidence < MOMENTUM_MIN_CONFIDENCE:
            return None

        # Parse confirmation from analysis_json
        confirmation = self._get_confirmation(analysis)
        if confirmation != "CONFIRMED":
            return None

        # Direction check
        if verdict in BEARISH_VERDICTS:
            return "BUY_PUT"
        elif verdict in BULLISH_VERDICTS:
            return "BUY_CALL"

        return None

    def _get_confirmation(self, analysis: dict) -> str:
        """Extract confirmation_status from analysis_json."""
        aj = analysis.get("analysis_json", "")
        if isinstance(aj, str) and aj:
            try:
                data = json.loads(aj)
                return data.get("confirmation_status", "")
            except (json.JSONDecodeError, TypeError):
                pass
        elif isinstance(aj, dict):
            return aj.get("confirmation_status", "")
        # Fallback: check if it's directly in analysis
        return analysis.get("confirmation_status", "")

    def _get_combined_score(self, analysis: dict) -> float:
        """Extract combined_score from analysis_json."""
        aj = analysis.get("analysis_json", "")
        if isinstance(aj, str) and aj:
            try:
                data = json.loads(aj)
                return data.get("combined_score", 0)
            except (json.JSONDecodeError, TypeError):
                pass
        elif isinstance(aj, dict):
            return aj.get("combined_score", 0)
        return analysis.get("combined_score", 0)

    def create_momentum_trade(self, direction: str, analysis: dict,
                               strikes_data: dict) -> Optional[int]:
        """Create a new momentum trade."""
        spot = analysis.get("spot_price", 0)
        verdict = analysis.get("verdict", "")
        confidence = analysis.get("signal_confidence", 0)
        iv_skew = analysis.get("iv_skew", 0) or 0
        vix = analysis.get("vix", 0) or 0
        combined_score = self._get_combined_score(analysis)
        confirmation = self._get_confirmation(analysis)

        # Strike: ATM
        strike = round(spot / NIFTY_STEP) * NIFTY_STEP
        option_type = "PE" if direction == "BUY_PUT" else "CE"

        # Get premium
        strike_data = strikes_data.get(strike, {})
        entry_premium = strike_data.get("pe_ltp" if option_type == "PE" else "ce_ltp", 0)

        if not entry_premium or entry_premium < MOMENTUM_MIN_PREMIUM:
            log.warning(f"Momentum skipped: premium too low",
                        strike=strike, premium=entry_premium)
            return None

        sl_premium = round(entry_premium * (1 - MOMENTUM_SL_PCT / 100), 2)
        target_premium = round(entry_premium * (1 + MOMENTUM_TARGET_PCT / 100), 2)

        trade_id = save_momentum_trade(
            direction=direction,
            strike=strike,
            option_type=option_type,
            entry_premium=entry_premium,
            sl_premium=sl_premium,
            target_premium=target_premium,
            spot=spot,
            verdict=verdict,
            confidence=confidence,
            iv_skew=iv_skew,
            vix=vix,
            combined_score=combined_score,
            confirmation_status=confirmation,
        )

        log.info(f"Created momentum trade",
                 trade_id=trade_id, direction=direction,
                 strike=f"{strike} {option_type}",
                 entry=entry_premium, sl=sl_premium, target=target_premium)

        self._send_entry_alert(direction, strike, option_type, entry_premium,
                                sl_premium, target_premium, spot, verdict,
                                confidence, iv_skew, vix, combined_score)
        return trade_id

    def check_and_update_momentum(self, strikes_data: dict) -> Optional[Dict]:
        """Check and update active momentum trade."""
        trade = get_active_momentum()
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

        update_momentum_trade(trade["id"],
                              last_checked_at=now,
                              last_premium=current,
                              max_premium_reached=max_p,
                              min_premium_reached=min_p)

        # Check SL
        if current <= trade["sl_premium"]:
            pnl = ((current - entry) / entry) * 100
            update_momentum_trade(trade["id"],
                                  status="LOST", resolved_at=now,
                                  exit_premium=current, exit_reason="SL",
                                  profit_loss_pct=pnl)
            log.info(f"Momentum LOST", pnl=f"{pnl:.2f}%", entry=entry, exit=current)
            self._send_exit_alert(trade, current, "SL", pnl)
            return {"action": "LOST", "pnl": pnl, "reason": "SL",
                    "strategy": STRATEGY_NAME}

        # Check Target
        if current >= trade["target_premium"]:
            pnl = ((current - entry) / entry) * 100
            update_momentum_trade(trade["id"],
                                  status="WON", resolved_at=now,
                                  exit_premium=current, exit_reason="TARGET",
                                  profit_loss_pct=pnl)
            log.info(f"Momentum WON", pnl=f"{pnl:.2f}%", entry=entry, exit=current)
            self._send_exit_alert(trade, current, "TARGET", pnl)
            return {"action": "WON", "pnl": pnl, "reason": "TARGET",
                    "strategy": STRATEGY_NAME}

        # EOD exit
        if now.time() >= FORCE_CLOSE_TIME:
            pnl = ((current - entry) / entry) * 100
            status = "WON" if pnl > 0 else "LOST"
            update_momentum_trade(trade["id"],
                                  status=status, resolved_at=now,
                                  exit_premium=current, exit_reason="EOD",
                                  profit_loss_pct=pnl)
            log.info(f"Momentum {status} EOD", pnl=f"{pnl:.2f}%")
            self._send_exit_alert(trade, current, "EOD", pnl)
            return {"action": status, "pnl": pnl, "reason": "EOD",
                    "strategy": STRATEGY_NAME}

        return None

    def _send_entry_alert(self, direction, strike, option_type, entry, sl,
                           target, spot, verdict, confidence, iv_skew, vix,
                           combined_score):
        """Send Telegram alert for new momentum trade."""
        try:
            from alerts import send_telegram

            dir_emoji = "🔴" if direction == "BUY_PUT" else "🟢"
            dir_text = "BUY PUT" if direction == "BUY_PUT" else "BUY CALL"

            message = (
                f"<b>🚀 MOMENTUM: {dir_text}</b>\n\n"
                f"<b>Direction:</b> <code>{dir_text}</code> {dir_emoji}\n"
                f"<b>Strike:</b> <code>{strike} {option_type}</code>\n"
                f"<b>Spot:</b> <code>{spot:.2f}</code>\n"
                f"<b>Entry:</b> <code>Rs {entry:.2f}</code>\n"
                f"<b>SL:</b> <code>Rs {sl:.2f}</code> (-{MOMENTUM_SL_PCT:.0f}%)\n"
                f"<b>Target:</b> <code>Rs {target:.2f}</code> (+{MOMENTUM_TARGET_PCT:.0f}%)\n"
                f"<b>RR:</b> <code>1:2</code>\n\n"
                f"<b>Why:</b> Triple alignment — OI, verdict, and price all agree\n\n"
                f"<b>Verdict:</b> {verdict} ({confidence:.0f}%)\n"
                f"<b>Score:</b> {combined_score:+.1f} | <b>Status:</b> CONFIRMED\n"
                f"<b>VIX:</b> {vix:.1f} | <b>IV Skew:</b> {iv_skew:.2f}\n\n"
                f"<i>Trend-following 1:2 RR — riding the momentum</i>\n"
                f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
            )
            send_telegram(message)
        except Exception as e:
            log.error("Failed to send momentum entry alert", error=str(e))

    def _send_exit_alert(self, trade, exit_premium, reason, pnl):
        """Send Telegram alert when momentum trade exits."""
        try:
            from alerts import send_telegram

            result_emoji = "✅" if pnl > 0 else "❌"
            dir_emoji = "🔴" if trade["direction"] == "BUY_PUT" else "🟢"

            reason_text = {
                "TARGET": f"Target Hit (+{MOMENTUM_TARGET_PCT:.0f}%)",
                "SL": f"Stop Loss (-{MOMENTUM_SL_PCT:.0f}%)",
                "EOD": "End of Day"
            }.get(reason, reason)

            message = (
                f"<b>{result_emoji} 🚀 Momentum {'WON' if pnl > 0 else 'LOST'}</b> {dir_emoji}\n\n"
                f"<b>Strike:</b> <code>{trade['strike']} {trade['option_type']}</code>\n"
                f"<b>Entry:</b> <code>Rs {trade['entry_premium']:.2f}</code>\n"
                f"<b>Exit:</b> <code>Rs {exit_premium:.2f}</code>\n"
                f"<b>P&L:</b> <code>{pnl:+.2f}%</code>\n"
                f"<b>Exit:</b> {reason_text}\n\n"
                f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
            )
            send_telegram(message)
        except Exception as e:
            log.error("Failed to send momentum exit alert", error=str(e))

    def get_momentum_stats(self) -> Dict:
        """Get momentum trade statistics."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) as losses,
                       SUM(profit_loss_pct) as total_pnl,
                       AVG(CASE WHEN status='WON' THEN profit_loss_pct END) as avg_win,
                       AVG(CASE WHEN status='LOST' THEN profit_loss_pct END) as avg_loss
                FROM momentum_trades
                WHERE status IN ('WON', 'LOST')
            """)
            row = cursor.fetchone()
            if row:
                r = dict(row)
                total = r['total'] or 0
                wins = r['wins'] or 0
                r['wr'] = (wins / total * 100) if total > 0 else 0
                return r
            return {'total': 0, 'wins': 0, 'losses': 0, 'total_pnl': 0, 'wr': 0}
