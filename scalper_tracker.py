"""
Scalper Tracker -- Trade lifecycle manager for FNO expert scalping trades.

Manages multiple quick scalp trades per day using the Claude Code agent
for premium chart analysis. Follows the same pattern as pa_tracker.py.

Key differences from other trackers:
- Multiple trades per day (max 5) with cooldown between trades
- Trades are shorter duration (targeting 3-15 minute holds)
- Uses Claude Code subprocess for trade signal generation
- Pre-filters with Python engine before calling Claude (saves cost)
"""

import os
from datetime import datetime, time, timedelta
from typing import Optional, Dict, List
from database import get_connection
from scalper_engine import ScalperEngine
from scalper_agent import ScalperAgent
from logger import get_logger

log = get_logger("scalper_tracker")

# Order placement config (from .env)
SCALP_PLACE_ORDER = os.getenv("SCALP_PLACE_ORDER", "false").lower() == "true"
SCALP_LOTS = int(os.getenv("SCALP_LOTS", "1"))
NIFTY_LOT_SIZE = int(os.getenv("NIFTY_LOT_SIZE", "65"))

# Strategy constants
SCALP_TIME_START = time(9, 30)
SCALP_TIME_END = time(14, 30)
SCALP_MAX_TRADES_PER_DAY = 5
SCALP_COOLDOWN_MINUTES = 6       # 2 candle cycles between trades
SCALP_MIN_PREMIUM = 50.0         # ITM options have higher premiums
SCALP_MAX_PREMIUM = 500.0
SCALP_MIN_AGENT_CONFIDENCE = 60  # Minimum Claude confidence to take trade
SCALP_FALLBACK_SL_PCT = 8.0     # If Claude doesn't specify good SL
SCALP_FALLBACK_TARGET_PCT = 10.0
SCALP_MAX_SL_PCT = 15.0          # Cap: never risk more than 15%
FORCE_CLOSE_TIME = time(15, 15)  # Earlier close for scalper
NIFTY_STEP = 50


def init_scalp_tables():
    """Create scalp trades table if it doesn't exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scalp_trades (
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
                vix_at_creation REAL,
                iv_at_creation REAL,
                vwap_at_creation REAL,
                agent_reasoning TEXT,
                status TEXT DEFAULT 'ACTIVE',
                resolved_at DATETIME,
                exit_premium REAL,
                exit_reason TEXT,
                profit_loss_pct REAL,
                max_premium_reached REAL,
                min_premium_reached REAL,
                last_checked_at DATETIME,
                last_premium REAL,
                trade_number INTEGER DEFAULT 1
            )
        """)
        conn.commit()
        log.info("Scalp tables initialized")


def get_active_scalp() -> Optional[Dict]:
    """Get currently active scalp trade."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM scalp_trades
            WHERE status = 'ACTIVE'
            ORDER BY created_at DESC LIMIT 1
        """)
        row = cursor.fetchone()
        return dict(row) if row else None


def get_todays_scalp_trades() -> List[Dict]:
    """Get all scalp trades created today."""
    today = datetime.now().strftime('%Y-%m-%d')
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM scalp_trades
            WHERE DATE(created_at) = ?
            ORDER BY created_at
        """, (today,))
        return [dict(r) for r in cursor.fetchall()]


def save_scalp_trade(created_at, direction, strike, option_type, entry_premium,
                     sl_premium, target_premium, spot, verdict, confidence,
                     vix, iv, vwap, reasoning, trade_number) -> int:
    """Save a new scalp trade."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO scalp_trades
            (created_at, direction, strike, option_type, entry_premium,
             sl_premium, target_premium, spot_at_creation, verdict_at_creation,
             signal_confidence, vix_at_creation, iv_at_creation, vwap_at_creation,
             agent_reasoning, status, max_premium_reached, min_premium_reached,
             trade_number)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
        """, (created_at, direction, strike, option_type, entry_premium,
              sl_premium, target_premium, spot, verdict, confidence,
              vix, iv, vwap, reasoning, entry_premium, entry_premium,
              trade_number))
        conn.commit()
        return cursor.lastrowid


def update_scalp_trade(trade_id: int, **kwargs):
    """Update a scalp trade."""
    if not kwargs:
        return
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [trade_id]
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE scalp_trades SET {set_clause} WHERE id = ?", values)
        conn.commit()


class ScalperTracker:
    """Manages FNO expert scalping trade lifecycle."""

    def __init__(self):
        init_scalp_tables()
        self.engine = ScalperEngine()
        self.agent = ScalperAgent()

    def should_create_scalp(self, analysis: Dict) -> bool:
        """
        Pre-checks before invoking Claude agent.
        Returns True if we should proceed with analysis.
        """
        now = datetime.now()

        # Time window check
        if now.time() < SCALP_TIME_START or now.time() > SCALP_TIME_END:
            return False

        # No active trade
        if get_active_scalp():
            return False

        # Max trades per day
        todays = get_todays_scalp_trades()
        if len(todays) >= SCALP_MAX_TRADES_PER_DAY:
            return False

        # Cooldown since last trade
        if todays:
            last = todays[-1]
            resolved_at = last.get("resolved_at")
            if resolved_at:
                last_resolved = datetime.fromisoformat(resolved_at)
                elapsed = (now - last_resolved).total_seconds()
                if elapsed < SCALP_COOLDOWN_MINUTES * 60:
                    log.debug("Scalp cooldown active",
                              elapsed=f"{elapsed:.0f}s",
                              required=f"{SCALP_COOLDOWN_MINUTES * 60}s")
                    return False
            else:
                # Last trade still active (shouldn't reach here due to check above)
                return False

        # Need spot price
        spot = analysis.get("spot_price", 0)
        if spot <= 0:
            return False

        # Need at least some candle data (skip first 15 min of market)
        if now.time() < time(9, 45):
            return False

        return True

    def get_agent_signal(self, analysis: Dict, strikes_data: Dict) -> Optional[Dict]:
        """
        Build chart -> run pre-filter -> call Claude -> return signal.
        This is the core method that invokes the Claude agent.
        """
        spot = analysis.get("spot_price", 0)
        strikes = self.engine.get_scalp_strikes(spot)
        ce_strike = strikes["ce_strike"]
        pe_strike = strikes["pe_strike"]

        # Build premium chart from DB
        chart = self.engine.build_premium_chart(spot, ce_strike=ce_strike, pe_strike=pe_strike)
        if not chart:
            log.info("No chart data available for scalper")
            return None

        # Need minimum candles for meaningful analysis
        min_candles = 5
        ce_ok = len(chart["ce_candles"]) >= min_candles
        pe_ok = len(chart["pe_candles"]) >= min_candles
        if not ce_ok and not pe_ok:
            log.debug("Not enough candles for scalper analysis",
                      ce=len(chart["ce_candles"]), pe=len(chart["pe_candles"]))
            return None

        # Pre-filter: check if either side has a potential setup
        ce_analysis = self.engine.analyze_side(chart["ce_candles"]) if ce_ok else {"has_setup": False}
        pe_analysis = self.engine.analyze_side(chart["pe_candles"]) if pe_ok else {"has_setup": False}

        if not ce_analysis["has_setup"] and not pe_analysis["has_setup"]:
            log.debug("No potential setup detected, skipping Claude call")
            return None

        # Format chart for Claude prompt
        chart_text = self.engine.format_chart_for_prompt(chart)

        # Get today's trade history for context
        todays = get_todays_scalp_trades()
        resolved = [t for t in todays if t.get("resolved_at")]

        # Call Claude agent
        signal = self.agent.get_signal(chart_text, analysis, resolved)
        if not signal:
            return None

        # Enrich signal with engine data
        side = signal.get("option_type", "CE")
        side_analysis = ce_analysis if side == "CE" else pe_analysis
        signal["_vwap"] = side_analysis.get("current_vwap", 0)
        signal["_candles"] = chart[f"{'ce' if side == 'CE' else 'pe'}_candles"]

        return signal

    def create_scalp_trade(self, signal: Dict, analysis: Dict) -> Optional[int]:
        """Create a new scalp trade from Claude's signal."""
        action = signal.get("action", "")
        option_type = signal.get("option_type", "")
        strike = signal.get("strike", 0)
        entry = signal.get("entry_premium", 0)
        sl = signal.get("sl_premium", 0)
        target = signal.get("target_premium", 0)
        confidence = signal.get("confidence", 0)
        reasoning = signal.get("reasoning", "")

        if not strike or not entry:
            log.warning("Invalid signal for trade creation", signal=signal)
            return None

        # Confidence check
        if confidence < SCALP_MIN_AGENT_CONFIDENCE:
            log.info("Scalp skipped: low confidence",
                     confidence=confidence, min=SCALP_MIN_AGENT_CONFIDENCE)
            return None

        # Premium range check
        if entry < SCALP_MIN_PREMIUM:
            log.info("Scalp skipped: premium too low", premium=entry)
            return None
        if entry > SCALP_MAX_PREMIUM:
            log.info("Scalp skipped: premium too high", premium=entry)
            return None

        # Cap SL risk
        sl_pct = (entry - sl) / entry * 100
        if sl_pct > SCALP_MAX_SL_PCT:
            sl = round(entry * (1 - SCALP_MAX_SL_PCT / 100), 2)
            log.info("SL capped", original_sl_pct=f"{sl_pct:.1f}%",
                     capped_to=f"{SCALP_MAX_SL_PCT}%", new_sl=sl)

        spot = analysis.get("spot_price", 0)
        verdict = analysis.get("verdict", "")
        vix = analysis.get("vix", 0) or 0

        # Get IV from candle data
        candles = signal.get("_candles", [])
        iv = candles[-1]["iv"] if candles else 0
        vwap = signal.get("_vwap", 0)

        direction = f"BUY_{option_type}"
        todays = get_todays_scalp_trades()
        trade_number = len(todays) + 1

        now = datetime.now()
        trade_id = save_scalp_trade(
            created_at=now,
            direction=direction,
            strike=strike,
            option_type=option_type,
            entry_premium=entry,
            sl_premium=sl,
            target_premium=target,
            spot=spot,
            verdict=verdict,
            confidence=confidence,
            vix=vix,
            iv=iv,
            vwap=vwap,
            reasoning=reasoning,
            trade_number=trade_number,
        )

        log.info("Created scalp trade",
                 trade_id=trade_id, direction=direction,
                 strike=f"{strike} {option_type}",
                 entry=entry, sl=sl, target=target,
                 confidence=confidence, trade_num=trade_number)

        self._send_entry_alert(direction, strike, option_type, entry, sl, target,
                               spot, verdict, confidence, vix, reasoning, trade_number)

        return trade_id

    def check_and_update_scalp(self, strikes_data: Dict) -> Optional[Dict]:
        """Check active scalp trade for SL/Target/EOD."""
        trade = get_active_scalp()
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

        update_scalp_trade(trade["id"],
                           last_checked_at=now,
                           last_premium=current,
                           max_premium_reached=max_p,
                           min_premium_reached=min_p)

        # Check SL
        if current <= trade["sl_premium"]:
            pnl = ((current - entry) / entry) * 100
            update_scalp_trade(trade["id"],
                               status="LOST", resolved_at=now,
                               exit_premium=current, exit_reason="SL",
                               profit_loss_pct=pnl)
            log.info("Scalp LOST (SL)", pnl=f"{pnl:.2f}%", entry=entry, exit=current)
            self._send_exit_alert(trade, current, "SL", pnl)
            return {"action": "LOST", "pnl": pnl, "reason": "SL"}

        # Check Target
        if current >= trade["target_premium"]:
            pnl = ((current - entry) / entry) * 100
            update_scalp_trade(trade["id"],
                               status="WON", resolved_at=now,
                               exit_premium=current, exit_reason="TARGET",
                               profit_loss_pct=pnl)
            log.info("Scalp WON (TARGET)", pnl=f"{pnl:.2f}%", entry=entry, exit=current)
            self._send_exit_alert(trade, current, "TARGET", pnl)
            return {"action": "WON", "pnl": pnl, "reason": "TARGET"}

        # EOD exit
        if now.time() >= FORCE_CLOSE_TIME:
            pnl = ((current - entry) / entry) * 100
            status = "WON" if pnl > 0 else "LOST"
            update_scalp_trade(trade["id"],
                               status=status, resolved_at=now,
                               exit_premium=current, exit_reason="EOD",
                               profit_loss_pct=pnl)
            log.info(f"Scalp {status} (EOD)", pnl=f"{pnl:.2f}%")
            self._send_exit_alert(trade, current, "EOD", pnl)
            return {"action": status, "pnl": pnl, "reason": "EOD"}

        return None

    def _send_entry_alert(self, direction, strike, option_type, entry, sl, target,
                          spot, verdict, confidence, vix, reasoning, trade_number):
        """Send Telegram alert for new scalp trade."""
        try:
            from alerts import send_telegram

            side_emoji = "\U0001f7e2" if option_type == "CE" else "\U0001f534"
            risk = entry - sl
            reward = target - entry
            rr = f"1:{reward/risk:.1f}" if risk > 0 else "N/A"
            sl_pct = (entry - sl) / entry * 100
            target_pct = (target - entry) / entry * 100

            message = (
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
            send_telegram(message)
        except Exception as e:
            log.error("Failed to send scalp entry alert", error=str(e))

    def _send_exit_alert(self, trade, exit_premium, reason, pnl):
        """Send Telegram alert when scalp trade exits."""
        try:
            from alerts import send_telegram

            result_emoji = "\u2705" if pnl > 0 else "\u274c"
            reason_text = {
                "TARGET": "Target Hit",
                "SL": "Stop Loss",
                "EOD": "End of Day",
            }.get(reason, reason)

            # Calculate trade duration
            created = datetime.fromisoformat(trade["created_at"]) if isinstance(trade["created_at"], str) else trade["created_at"]
            duration = datetime.now() - created
            duration_str = f"{int(duration.total_seconds() / 60)}m"

            message = (
                f"<b>{result_emoji} SCALP {'WON' if pnl > 0 else 'LOST'}</b> (#{trade.get('trade_number', '?')})\n\n"
                f"<b>Strike:</b> <code>{trade['strike']} {trade['option_type']}</code>\n"
                f"<b>Entry:</b> <code>Rs {trade['entry_premium']:.2f}</code>\n"
                f"<b>Exit:</b> <code>Rs {exit_premium:.2f}</code>\n"
                f"<b>P&L:</b> <code>{pnl:+.2f}%</code>\n"
                f"<b>Duration:</b> {duration_str}\n"
                f"<b>Reason:</b> {reason_text}\n\n"
                f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
            )
            send_telegram(message)
        except Exception as e:
            log.error("Failed to send scalp exit alert", error=str(e))

    def get_scalp_stats(self) -> Dict:
        """Get scalp trade statistics."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) as losses,
                       SUM(profit_loss_pct) as total_pnl,
                       AVG(CASE WHEN status='WON' THEN profit_loss_pct END) as avg_win,
                       AVG(CASE WHEN status='LOST' THEN profit_loss_pct END) as avg_loss
                FROM scalp_trades
                WHERE status IN ('WON', 'LOST')
            """)
            overall = dict(cursor.fetchone())

            # Today's stats
            today = datetime.now().strftime('%Y-%m-%d')
            cursor.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) as losses,
                       SUM(profit_loss_pct) as total_pnl
                FROM scalp_trades
                WHERE DATE(created_at) = ? AND status IN ('WON', 'LOST')
            """, (today,))
            today_stats = dict(cursor.fetchone())

            return {"overall": overall, "today": today_stats}
