import os, sys, hashlib, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'), override=True)
from kite_auth import save_token

api_key = os.environ.get('KITE_API_KEY')
api_secret = os.environ.get('KITE_API_SECRET')
request_token = 'yJ7ljL6PNT1JeJDlzYfjGA54fe9nuaH2'

checksum = hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()

resp = requests.post("https://api.kite.trade/session/token", data={
    'api_key': api_key,
    'request_token': request_token,
    'checksum': checksum
}, timeout=10)

result = resp.json()
print(f"Response: {result}")

if result.get('status') == 'success':
    token = result['data']['access_token']
    save_token(token)
    print(f"\nAccess token: {token[:15]}...")
    print("Saved! Now verifying...")
    
    r = requests.get('https://api.kite.trade/user/profile',
        headers={'X-Kite-Version': '3', 'Authorization': f'token {api_key}:{token}'}, timeout=10)
    print(f"Profile: {r.json()}")
