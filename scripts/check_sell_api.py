import json, urllib.request
data = json.loads(urllib.request.urlopen('http://localhost:5000/api/latest').read())
print("active_sell_trade:", json.dumps(data.get('active_sell_trade'), indent=2))
print("sell_stats:", json.dumps(data.get('sell_stats'), indent=2))
