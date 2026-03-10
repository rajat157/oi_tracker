"""Alerts package — Telegram notification system.

Re-exports legacy functions for backwards compatibility
(``from alerts import send_telegram`` keeps working everywhere).
Also exposes the new TelegramChannel and AlertBroker for EventBus integration.
"""

# Legacy functions — used by all existing trackers
from alerts._legacy import (  # noqa: F401
    send_telegram,
    send_telegram_multi,
    send_trade_setup_alert,
    send_pm_reversal_alert,
    send_test_alert,
    _get_kite_trading_symbol,
    _get_kite_chart_url,
    _get_kite_basket_url,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    SELLING_ALERT_CHAT_IDS,
    SELLING_ALERT_BOT_TOKEN,
    SELLING_ALERT_EXTRA_CHAT_IDS,
    ALERT_COOLDOWN,
)

# New OOP alert system
from alerts.telegram import TelegramChannel  # noqa: F401
from alerts.broker import AlertBroker  # noqa: F401
