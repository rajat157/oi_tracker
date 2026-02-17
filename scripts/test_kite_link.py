"""Send a test Iron Pulse alert with Kite basket link."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'), override=True)

from alerts import send_telegram, _get_kite_trading_symbol, _get_kite_basket_url

strike = 25600
ot = 'CE'
entry = 51.50
sl = 41.20
t1 = 62.83
symbol = _get_kite_trading_symbol(strike, ot, '2026-02-20')
basket_url = _get_kite_basket_url(strike, ot, entry, 65, '2026-02-20')

msg = f'''<b>\U0001f9ea TEST \u2014 Iron Pulse Order Link v2</b>

<b>Strike:</b> <code>{strike} {ot}</code>
<b>Entry:</b> <code>Rs {entry}</code> | <b>SL:</b> <code>Rs {sl}</code> | <b>T1:</b> <code>Rs {t1}</code>

\U0001f680 <a href="{basket_url}">Place Order on Kite</a>

<i>Tap the link to open Kite with order pre-filled!</i>
'''

print(f'API Key: {os.environ.get("KITE_API_KEY", "NOT SET")}')
print(f'Symbol: {symbol}')
print(f'URL: {basket_url[:100]}...')
result = send_telegram(msg)
print(f'Sent: {result}')
