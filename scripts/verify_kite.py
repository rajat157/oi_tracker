import os, sys, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'), override=True)
from kite_auth import load_token

token = load_token()
api_key = os.environ.get('KITE_API_KEY', '')
print(f"Token: {token[:15]}..." if token else "No token!")
print(f"API Key: {api_key}")

r = requests.get('https://api.kite.trade/user/profile', 
    headers={'X-Kite-Version': '3', 'Authorization': f'token {api_key}:{token}'}, timeout=10)
data = r.json()
print(f"Response: {data}")
if data.get('status') == 'success':
    print(f"\nLogged in as: {data['data']['user_name']} ({data['data']['user_id']})")
    print("Kite is READY for order placement!")
