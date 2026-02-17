import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'), override=True)
from alerts import send_telegram, _get_kite_chart_url

# Today is Monday Feb 17, next expiry is Feb 24 (weekly = monthly this week)
chart_url, symbol = _get_kite_chart_url(25600, 'CE', '2026-02-24')
print(f"Symbol: {symbol}")
print(f"URL: {chart_url}")

if chart_url:
    msg = (f'<b>\U0001f9ea TEST \u2014 Iron Pulse (Kite Chart Link)</b>\n\n'
           f'<b>Strike:</b> <code>25600 CE</code>\n'
           f'<b>Entry:</b> Rs 51.50 | SL: Rs 41.20 | T1: Rs 62.83\n\n'
           f'\U0001f680 <a href="{chart_url}">Open in Kite</a>\n'
           f'<code>{symbol} | BUY LIMIT Rs 51.50 | Qty 65</code>\n\n'
           f'<i>Click link \u2192 opens chart \u2192 click Buy</i>')
    send_telegram(msg)
    print("Sent!")
else:
    print(f"No token found for {symbol}")
    # Try alternate: monthly format
    chart_url2, symbol2 = _get_kite_chart_url(25600, 'CE', '2026-02-17')
    print(f"Today's expiry: {symbol2}, URL: {chart_url2}")
