import json, urllib.parse, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'), override=True)
from alerts import send_telegram, _get_kite_trading_symbol

strike = 25600
ot = 'CE'
entry = 51.50
sl = 41.20
t1 = 62.83
symbol = _get_kite_trading_symbol(strike, ot, '2026-02-20')
api_key = os.environ.get('KITE_API_KEY')

basket = [{"variety": "regular", "tradingsymbol": symbol, "exchange": "NFO",
           "transaction_type": "BUY", "order_type": "LIMIT", "price": entry,
           "quantity": 65, "product": "MIS", "readonly": False}]
encoded = urllib.parse.quote(json.dumps(basket))
url = f"https://kite.zerodha.com/connect/basket?api_key={api_key}&data={encoded}"

msg = (f'<b>\U0001f9ea TEST \u2014 Iron Pulse Order Link</b>\n\n'
       f'<b>Strike:</b> <code>{strike} {ot}</code>\n'
       f'<b>Entry:</b> Rs {entry} | SL: Rs {sl} | T1: Rs {t1}\n\n'
       f'\U0001f680 <a href="{url}">Place Order on Kite</a>\n\n'
       f'<i>Click from laptop browser (must be logged into Kite web)</i>')

print(f'API Key: {api_key}')
print(f'Symbol: {symbol}')
result = send_telegram(msg)
print(f'Sent: {result}')
