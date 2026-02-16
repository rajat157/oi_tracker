"""
Telegram Alert System for OI Tracker
Sends notifications for high-probability trade setups
"""

import os
import requests
from datetime import datetime
from pathlib import Path

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not installed, use env vars directly

from logger import get_logger

log = get_logger("alerts")

# Configuration - set these in environment or .env file
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7011095516")  # Default: Mason's chat
SELLING_ALERT_CHAT_IDS = [x.strip() for x in os.getenv("SELLING_ALERT_CHAT_IDS", TELEGRAM_CHAT_ID).split(",")]
SELLING_ALERT_BOT_TOKEN = os.getenv("SELLING_ALERT_BOT_TOKEN", "")  # Separate bot for external users
SELLING_ALERT_EXTRA_CHAT_IDS = [x.strip() for x in os.getenv("SELLING_ALERT_EXTRA_CHAT_IDS", "").split(",") if x.strip()]

# Alert cooldown to prevent spam (seconds)
ALERT_COOLDOWN = 300  # 5 minutes between same-type alerts

# Track last alert times
_last_alerts = {}


def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """
    Send a message via Telegram bot.
    
    Args:
        message: The message text to send
        parse_mode: HTML or Markdown
        
    Returns:
        True if sent successfully, False otherwise
    """
    if not TELEGRAM_BOT_TOKEN:
        log.warning("Telegram bot token not configured - alert not sent")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            log.info("Telegram alert sent successfully")
            return True
        else:
            log.error("Telegram API error", status=response.status_code, response=response.text)
            return False
    except Exception as e:
        log.error("Failed to send Telegram alert", error=str(e))
        return False


def send_telegram_multi(message: str, chat_ids: list, parse_mode: str = "HTML") -> bool:
    """Send selling alerts to Mason (main bot) + external users (separate bot)."""
    success = True
    # Send to Mason via main bot
    for cid in chat_ids:
        if not TELEGRAM_BOT_TOKEN:
            return False
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": cid, "text": message, "parse_mode": parse_mode, "disable_web_page_preview": True}
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                log.info("Telegram alert sent", chat_id=cid)
            else:
                log.error("Telegram API error", chat_id=cid, status=response.status_code)
                success = False
        except Exception as e:
            log.error("Failed to send Telegram alert", chat_id=cid, error=str(e))
            success = False
    # Send to external users via separate bot
    if SELLING_ALERT_BOT_TOKEN and SELLING_ALERT_EXTRA_CHAT_IDS:
        for cid in SELLING_ALERT_EXTRA_CHAT_IDS:
            url = f"https://api.telegram.org/bot{SELLING_ALERT_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": cid, "text": message, "parse_mode": parse_mode, "disable_web_page_preview": True}
            try:
                response = requests.post(url, json=payload, timeout=10)
                if response.status_code == 200:
                    log.info("External alert sent", chat_id=cid)
                else:
                    log.error("External alert error", chat_id=cid, status=response.status_code)
                    success = False
            except Exception as e:
                log.error("Failed to send external alert", chat_id=cid, error=str(e))
                success = False
    return success


def _check_cooldown(alert_type: str) -> bool:
    """Check if we're in cooldown period for this alert type."""
    now = datetime.now()
    last_time = _last_alerts.get(alert_type)
    
    if last_time:
        elapsed = (now - last_time).total_seconds()
        if elapsed < ALERT_COOLDOWN:
            log.debug("Alert in cooldown", alert_type=alert_type, remaining=f"{ALERT_COOLDOWN - elapsed:.0f}s")
            return False
    
    _last_alerts[alert_type] = now
    return True


def send_pm_reversal_alert(
    spot_price: float,
    pm_score: float,
    pm_change: float,
    confidence: float,
    strike: int,
    entry_premium: float,
    target_premium: float,
    sl_premium: float
) -> bool:
    """
    Send alert for PM Reversal CALL entry signal.
    
    Triggered when:
    - PM was very negative (< -50)
    - PM has now crossed above +50 (strong reversal confirmed)
    
    This is NOT the first reversal signal - it's the CONFIRMED one.
    """
    alert_type = "PM_REVERSAL_CALL"
    
    if not _check_cooldown(alert_type):
        return False
    
    # Calculate risk/reward
    risk = entry_premium - sl_premium
    reward = target_premium - entry_premium
    rr_ratio = reward / risk if risk > 0 else 0
    
    now = datetime.now()
    
    message = f"""
<b>🟢 CALL ENTRY SIGNAL</b>

<b>PM Reversal Confirmed</b>
PM Score: <code>{pm_score:+.1f}</code> (was negative, now strong positive)
PM Change: <code>{pm_change:+.1f}</code>
Signal Confidence: <code>{confidence:.0f}%</code>

<b>Setup Details</b>
Spot: <code>{spot_price:.2f}</code>
Strike: <code>{strike} CE</code>
Entry: <code>₹{entry_premium:.2f}</code>
Target: <code>₹{target_premium:.2f}</code> (+{reward:.1f})
SL: <code>₹{sl_premium:.2f}</code> (-{risk:.1f})
R:R = <code>1:{rr_ratio:.1f}</code>

<b>Strategy</b>
• Wait for pullback if entry seems high
• Target: +40 pts on spot (or premium target)
• SL: -50 pts on spot (or premium SL)
• Max hold: 2 hours

<i>Time: {now.strftime('%H:%M:%S')}</i>
"""
    
    return send_telegram(message.strip())


def send_trade_setup_alert(
    direction: str,
    strike: str,
    entry_premium: float,
    sl_premium: float,
    target_premium: float,
    sl_pct: float,
    target_pct: float,
    verdict: str,
    confidence: float
) -> bool:
    """
    Send Telegram alert when a new trade setup is created.
    
    NEW STRATEGY (85.7% Win Rate):
    - Time Window: 11:00 - 14:00 IST
    - Only "Slightly" verdicts
    - Confidence >= 65%
    - ONE trade per day
    
    Args:
        direction: BUY CALL or BUY PUT
        strike: Strike with option type (e.g., "25750 CE")
        entry_premium: Entry price
        sl_premium: Stop loss price
        target_premium: Target price
        sl_pct: Stop loss percentage (e.g., 20.0)
        target_pct: Target percentage (e.g., 22.0)
        verdict: Market verdict
        confidence: Signal confidence percentage
    
    Returns:
        True if sent successfully
    """
    alert_type = "TRADE_SETUP"
    
    if not _check_cooldown(alert_type):
        return False
    
    now = datetime.now()
    
    message = f"""
<b>🫀 Iron Pulse — TRADE SETUP</b>

<b>Direction:</b> <code>{direction}</code>
<b>Strike:</b> <code>{strike}</code>
<b>Entry:</b> <code>Rs {entry_premium:.2f}</code>
<b>Stop Loss:</b> <code>Rs {sl_premium:.2f}</code> (-{sl_pct:.0f}%)
<b>Target:</b> <code>Rs {target_premium:.2f}</code> (+{target_pct:.0f}%)
<b>RR:</b> <code>1:1</code>

<b>Verdict:</b> {verdict}
<b>Confidence:</b> {confidence:.0f}%

⏰ <i>Valid until 14:00 or entry hit</i>
📊 <i>One trade per day — bread &amp; butter!</i>

<i>Time: {now.strftime('%H:%M:%S')}</i>
"""
    
    log.info("Sending trade setup alert", direction=direction, strike=strike,
             entry=f"₹{entry_premium:.2f}", sl=f"₹{sl_premium:.2f}", 
             target=f"₹{target_premium:.2f}")
    
    return send_telegram(message.strip())


def send_test_alert() -> bool:
    """Send a test alert to verify configuration."""
    message = f"""
<b>🔔 OI Tracker Alert Test</b>

If you see this, alerts are working!

<i>Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>
"""
    return send_telegram(message.strip())


if __name__ == "__main__":
    # Test the alert system
    print("Testing Telegram alerts...")
    
    if not TELEGRAM_BOT_TOKEN:
        print("⚠️  TELEGRAM_BOT_TOKEN not set!")
        print("Set it in environment: export TELEGRAM_BOT_TOKEN='your-bot-token'")
    else:
        result = send_test_alert()
        print(f"Test alert sent: {result}")
