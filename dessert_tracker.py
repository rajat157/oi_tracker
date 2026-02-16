"""
Dessert Tracker — Premium 1:2 RR buying strategies.
These are independent of Config B (the bread & butter 1:1 trades).
One dessert trade per day max (first strategy to trigger wins).

Strategy 1: "Contra Sniper" (100% backtested WR)
  Filters: BUY_PUT + IV Skew < 1 + Strike Below Max Pain + Contra verdict (Bullish)
  Logic: When market looks bullish but price is below max pain with low IV skew,
         the crowd is wrong — buy PUT for reversal.

Strategy 2: "Phantom PUT" (83.3% backtested WR)
  Filters: BUY_PUT + Confidence < 50% + IV Skew < 0 + Spot Rising (30min)
  Logic: Low confidence + negative IV skew + rising spot = uncertainty before reversal.
         The system doesn't see it coming — hence "Phantom".

Both use: SL 25%, Target 50% (1:2 RR), ATM strike, 9:30-14:00 window.
"""

from datetime import datetime, time, timedelta
from typing import Optional, Dict, List
from database import get_connection
from logger import get_logger

log = get_logger("dessert_tracker")

# Strategy constants
DESSERT_TIME_START = time(9, 30)
DESSERT_TIME_END = time(14, 0)
DESSERT_SL_PCT = 25.0
DESSERT_TARGET_PCT = 50.0    # 1:2 RR
DESSERT_MIN_PREMIUM = 5.0
FORCE_CLOSE_TIME = time(15, 20)
NIFTY_STEP = 50

# Strategy names
CONTRA_SNIPER = "Contra Sniper"
PHANTOM_PUT = "Phantom PUT"


def init_dessert_tables():
    """Create dessert trade tables if they don't exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dessert_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME NOT NULL,
                strategy_name TEXT NOT NULL,
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
                max_pain_at_creation REAL,
                spot_move_30m REAL,
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
        log.info("Dessert tables initialized")


def get_active_dessert() -> Optional[Dict]:
    """Get currently active dessert trade."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM dessert_trades
            WHERE status = 'ACTIVE'
            ORDER BY created_at DESC LIMIT 1
        """)
        row = cursor.fetchone()
        return dict(row) if row else None


def get_todays_dessert_trades() -> List[Dict]:
    """Get all dessert trades created today."""
    today = datetime.now().strftime('%Y-%m-%d')
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM dessert_trades
            WHERE DATE(created_at) = ?
            ORDER BY created_at
        """, (today,))
        return [dict(r) for r in cursor.fetchall()]


def save_dessert_trade(strategy_name, created_at, direction, strike, option_type,
                       entry_premium, sl_premium, target_premium, spot, verdict,
                       confidence, iv_skew, vix, max_pain, spot_move_30m) -> int:
    """Save a new dessert trade."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO dessert_trades
            (strategy_name, created_at, direction, strike, option_type, entry_premium,
             sl_premium, target_premium, spot_at_creation, verdict_at_creation,
             signal_confidence, iv_skew_at_creation, vix_at_creation, max_pain_at_creation,
             spot_move_30m, status, max_premium_reached, min_premium_reached)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
        """, (strategy_name, created_at, direction, strike, option_type, entry_premium,
              sl_premium, target_premium, spot, verdict, confidence, iv_skew, vix,
              max_pain, spot_move_30m, entry_premium, entry_premium))
        conn.commit()
        return cursor.lastrowid


def update_dessert_trade(trade_id: int, **kwargs):
    """Update a dessert trade."""
    if not kwargs:
        return
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [trade_id]
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE dessert_trades SET {set_clause} WHERE id = ?", values)
        conn.commit()


def _get_spot_move_30m(conn) -> Optional[float]:
    """Calculate spot price movement over last 30 minutes from analysis_history."""
    now = datetime.now()
    thirty_ago = now - timedelta(minutes=30)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT spot_price, timestamp FROM analysis_history
        WHERE timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp
    """, (thirty_ago.strftime('%Y-%m-%d %H:%M:%S'), now.strftime('%Y-%m-%d %H:%M:%S')))
    rows = cursor.fetchall()
    if len(rows) < 2:
        return None
    first = rows[0]['spot_price']
    last = rows[-1]['spot_price']
    return (last - first) / first * 100


class DessertTracker:
    """Manages premium 1:2 RR buying strategies."""

    def __init__(self):
        init_dessert_tables()

    def _check_contra_sniper(self, analysis: dict) -> bool:
        """
        Contra Sniper: BUY_PUT when verdict is Bullish + IV Skew < 1 + Below Max Pain
        """
        verdict = analysis.get("verdict", "")
        iv_skew = analysis.get("iv_skew", 0) or 0
        spot = analysis.get("spot_price", 0)
        max_pain = analysis.get("max_pain", 0) or 0
        
        # Must be bullish verdict (we're going contra)
        if "Bull" not in verdict:
            return False
        
        # IV Skew < 1
        if iv_skew >= 1:
            return False
        
        # ATM strike must be below max pain
        atm = round(spot / NIFTY_STEP) * NIFTY_STEP
        if max_pain <= 0 or atm >= max_pain:
            return False
        
        return True

    def _check_phantom_put(self, analysis: dict, spot_move_30m: Optional[float]) -> bool:
        """
        Phantom PUT: BUY_PUT when conf < 50% + IV Skew < 0 + Spot rising
        """
        confidence = analysis.get("signal_confidence", 0) or 0
        iv_skew = analysis.get("iv_skew", 0) or 0
        
        # Confidence < 50%
        if confidence >= 50:
            return False
        
        # IV Skew < 0
        if iv_skew >= 0:
            return False
        
        # Spot rising in last 30 min (> 0.05%)
        if spot_move_30m is None or spot_move_30m <= 0.05:
            return False
        
        return True

    def should_create_dessert(self, analysis: dict) -> Optional[str]:
        """
        Check if any dessert strategy triggers.
        Returns strategy name or None.
        """
        # One dessert per day
        if get_todays_dessert_trades():
            return None
        
        # No active dessert
        if get_active_dessert():
            return None
        
        # Time window
        now = datetime.now().time()
        if now < DESSERT_TIME_START or now > DESSERT_TIME_END:
            return None
        
        # Get spot movement
        with get_connection() as conn:
            spot_move = _get_spot_move_30m(conn)
        
        # Check strategies in priority order
        # Contra Sniper first (100% backtested WR)
        if self._check_contra_sniper(analysis):
            return CONTRA_SNIPER
        
        # Phantom PUT second (83.3% backtested WR)
        if self._check_phantom_put(analysis, spot_move):
            return PHANTOM_PUT
        
        return None

    def create_dessert_trade(self, strategy_name: str, analysis: dict,
                              strikes_data: dict) -> Optional[int]:
        """Create a new dessert trade (always BUY_PUT at ATM)."""
        spot = analysis.get("spot_price", 0)
        verdict = analysis.get("verdict", "")
        confidence = analysis.get("signal_confidence", 0)
        iv_skew = analysis.get("iv_skew", 0) or 0
        vix = analysis.get("vix", 0) or 0
        max_pain = analysis.get("max_pain", 0) or 0
        
        # Always BUY PUT at ATM
        direction = "BUY_PUT"
        option_type = "PE"
        strike = round(spot / NIFTY_STEP) * NIFTY_STEP
        
        # Get premium
        strike_data = strikes_data.get(strike, {})
        entry_premium = strike_data.get("pe_ltp", 0)
        
        if not entry_premium or entry_premium < DESSERT_MIN_PREMIUM:
            log.warning(f"Dessert ({strategy_name}) skipped: premium too low",
                       strike=strike, premium=entry_premium)
            return None
        
        sl_premium = round(entry_premium * (1 - DESSERT_SL_PCT / 100), 2)
        target_premium = round(entry_premium * (1 + DESSERT_TARGET_PCT / 100), 2)
        
        # Get spot movement
        with get_connection() as conn:
            spot_move = _get_spot_move_30m(conn)
        
        now = datetime.now()
        trade_id = save_dessert_trade(
            strategy_name=strategy_name,
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
            iv_skew=iv_skew,
            vix=vix,
            max_pain=max_pain,
            spot_move_30m=spot_move or 0
        )
        
        log.info(f"Created dessert trade: {strategy_name}",
                 trade_id=trade_id, strike=f"{strike} PE",
                 entry=entry_premium, sl=sl_premium, target=target_premium)
        
        self._send_entry_alert(strategy_name, strike, entry_premium, sl_premium,
                                target_premium, spot, verdict, confidence, iv_skew,
                                vix, max_pain, spot_move)
        
        return trade_id

    def check_and_update_dessert(self, strikes_data: dict) -> Optional[Dict]:
        """Check and update active dessert trade."""
        trade = get_active_dessert()
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
        
        update_dessert_trade(trade["id"],
                            last_checked_at=now,
                            last_premium=current,
                            max_premium_reached=max_p,
                            min_premium_reached=min_p)
        
        # Check SL (premium drops = loss for buyer)
        if current <= trade["sl_premium"]:
            pnl = ((current - entry) / entry) * 100
            update_dessert_trade(trade["id"],
                                status="LOST", resolved_at=now,
                                exit_premium=current, exit_reason="SL",
                                profit_loss_pct=pnl)
            log.info(f"Dessert LOST ({trade['strategy_name']})",
                     pnl=f"{pnl:.2f}%", entry=entry, exit=current)
            self._send_exit_alert(trade, current, "SL", pnl)
            return {"action": "LOST", "pnl": pnl, "reason": "SL",
                    "strategy": trade["strategy_name"]}
        
        # Check Target (premium rises = win for buyer)
        if current >= trade["target_premium"]:
            pnl = ((current - entry) / entry) * 100
            update_dessert_trade(trade["id"],
                                status="WON", resolved_at=now,
                                exit_premium=current, exit_reason="TARGET",
                                profit_loss_pct=pnl)
            log.info(f"Dessert WON ({trade['strategy_name']})",
                     pnl=f"{pnl:.2f}%", entry=entry, exit=current)
            self._send_exit_alert(trade, current, "TARGET", pnl)
            return {"action": "WON", "pnl": pnl, "reason": "TARGET",
                    "strategy": trade["strategy_name"]}
        
        # EOD exit
        if now.time() >= FORCE_CLOSE_TIME:
            pnl = ((current - entry) / entry) * 100
            status = "WON" if pnl > 0 else "LOST"
            update_dessert_trade(trade["id"],
                                status=status, resolved_at=now,
                                exit_premium=current, exit_reason="EOD",
                                profit_loss_pct=pnl)
            log.info(f"Dessert {status} EOD ({trade['strategy_name']})",
                     pnl=f"{pnl:.2f}%")
            self._send_exit_alert(trade, current, "EOD", pnl)
            return {"action": status, "pnl": pnl, "reason": "EOD",
                    "strategy": trade["strategy_name"]}
        
        return None

    def _send_entry_alert(self, strategy, strike, entry, sl, target,
                           spot, verdict, confidence, iv_skew, vix, max_pain, spot_move):
        """Send Telegram alert for new dessert trade."""
        try:
            from alerts import send_telegram
            
            if strategy == CONTRA_SNIPER:
                emoji = "🎯"
                desc = "Crowd says Bullish, but price below Max Pain + low IV skew = reversal incoming"
            else:
                emoji = "🔮"
                desc = "Low confidence + negative IV skew + rising spot = hidden reversal"
            
            message = (
                f"<b>{emoji} DESSERT: {strategy}</b>\n\n"
                f"<b>Direction:</b> <code>BUY PUT</code>\n"
                f"<b>Strike:</b> <code>{strike} PE</code>\n"
                f"<b>Spot:</b> <code>{spot:.2f}</code>\n"
                f"<b>Entry:</b> <code>Rs {entry:.2f}</code>\n"
                f"<b>SL:</b> <code>Rs {sl:.2f}</code> (-{DESSERT_SL_PCT:.0f}%)\n"
                f"<b>Target:</b> <code>Rs {target:.2f}</code> (+{DESSERT_TARGET_PCT:.0f}%)\n"
                f"<b>RR:</b> <code>1:2</code>\n\n"
                f"<b>Why:</b> {desc}\n\n"
                f"<b>Verdict:</b> {verdict} ({confidence:.0f}%)\n"
                f"<b>VIX:</b> {vix:.1f} | <b>IV Skew:</b> {iv_skew:.2f}\n"
                f"<b>Max Pain:</b> {max_pain:.0f}\n"
                f"<b>Spot 30m:</b> {spot_move:.3f}%\n\n"
                f"<i>This is a 1:2 RR dessert trade. Take it if it looks good!</i>\n"
                f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
            )
            send_telegram(message)
        except Exception as e:
            log.error("Failed to send dessert entry alert", error=str(e))

    def _send_exit_alert(self, trade, exit_premium, reason, pnl):
        """Send Telegram alert when dessert trade exits."""
        try:
            from alerts import send_telegram
            
            strategy = trade["strategy_name"]
            if strategy == CONTRA_SNIPER:
                emoji = "🎯"
            else:
                emoji = "🔮"
            
            result_emoji = "✅" if pnl > 0 else "❌"
            
            reason_text = {
                "TARGET": "Target Hit (+50%)",
                "SL": "Stop Loss (-25%)",
                "EOD": "End of Day"
            }.get(reason, reason)
            
            message = (
                f"<b>{result_emoji} {emoji} {strategy} {'WON' if pnl > 0 else 'LOST'}</b>\n\n"
                f"<b>Strike:</b> <code>{trade['strike']} PE</code>\n"
                f"<b>Entry:</b> <code>Rs {trade['entry_premium']:.2f}</code>\n"
                f"<b>Exit:</b> <code>Rs {exit_premium:.2f}</code>\n"
                f"<b>P&L:</b> <code>{pnl:+.2f}%</code>\n"
                f"<b>Exit:</b> {reason_text}\n\n"
                f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
            )
            send_telegram(message)
        except Exception as e:
            log.error("Failed to send dessert exit alert", error=str(e))

    def get_dessert_stats(self) -> Dict:
        """Get dessert trade statistics."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT strategy_name,
                       COUNT(*) as total,
                       SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) as losses,
                       SUM(profit_loss_pct) as total_pnl
                FROM dessert_trades
                WHERE status IN ('WON', 'LOST')
                GROUP BY strategy_name
            """)
            results = {}
            for row in cursor.fetchall():
                r = dict(row)
                results[r['strategy_name']] = r
            
            # Overall stats
            cursor.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) as losses,
                       SUM(profit_loss_pct) as total_pnl
                FROM dessert_trades
                WHERE status IN ('WON', 'LOST')
            """)
            overall = dict(cursor.fetchone())
            results['overall'] = overall
            return results
