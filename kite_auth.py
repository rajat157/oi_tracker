"""
Kite Connect OAuth login flow.
Run this once each morning to get a daily access token.

Usage: python kite_auth.py
Opens browser -> login -> captures token -> stores in .env
"""
import os
import hashlib
import webbrowser
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv, set_key
from datetime import datetime

load_dotenv()

API_KEY = os.environ.get('KITE_API_KEY', '')
API_SECRET = os.environ.get('KITE_API_SECRET', '')
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.kite_token')

LOGIN_URL = f"https://kite.zerodha.com/connect/login?v=3&api_key={API_KEY}"
TOKEN_URL = "https://api.kite.trade/session/token"


class TokenCaptureHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the request_token from Kite's redirect."""
    
    request_token = None
    
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        
        if 'request_token' in params:
            TokenCaptureHandler.request_token = params['request_token'][0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b"""
            <html><body style="font-family:sans-serif;text-align:center;padding:50px;">
            <h1 style="color:green;">Login Successful!</h1>
            <p>Access token captured. You can close this tab.</p>
            <p>Iron Pulse is ready to trade.</p>
            </body></html>
            """)
        else:
            self.send_response(400)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Error: No token received</h1></body></html>")
    
    def log_message(self, format, *args):
        pass  # Suppress logs


def exchange_token(request_token: str) -> dict:
    """Exchange request_token for access_token."""
    checksum = hashlib.sha256(f"{API_KEY}{request_token}{API_SECRET}".encode()).hexdigest()
    
    resp = requests.post(TOKEN_URL, data={
        'api_key': API_KEY,
        'request_token': request_token,
        'checksum': checksum
    })
    
    return resp.json()


def save_token(access_token: str):
    """Save access token to file and .env."""
    # Save to token file with timestamp
    with open(TOKEN_FILE, 'w') as f:
        f.write(f"{access_token}\n{datetime.now().isoformat()}")
    
    # Also save to .env
    set_key(ENV_PATH, 'KITE_ACCESS_TOKEN', access_token)
    print(f"Access token saved to {TOKEN_FILE} and .env")


def load_token() -> str:
    """Load today's access token if available."""
    if not os.path.exists(TOKEN_FILE):
        return ""
    
    with open(TOKEN_FILE, 'r') as f:
        lines = f.read().strip().split('\n')
    
    if len(lines) < 2:
        return ""
    
    token = lines[0]
    saved_date = lines[1][:10]  # YYYY-MM-DD
    today = datetime.now().strftime('%Y-%m-%d')
    
    if saved_date == today:
        return token
    return ""


def login():
    """Run the full login flow."""
    if not API_KEY or not API_SECRET:
        print("ERROR: KITE_API_KEY and KITE_API_SECRET must be set in .env")
        return False
    
    # Check for existing token
    existing = load_token()
    if existing:
        print(f"Today's token already exists: {existing[:10]}...")
        reuse = input("Use existing token? (y/n): ").strip().lower()
        if reuse == 'y':
            return True
    
    print(f"\nOpening Kite login in browser...")
    print(f"URL: {LOGIN_URL}\n")
    
    # Start local server to capture redirect
    server = HTTPServer(('127.0.0.1', 80), TokenCaptureHandler)
    server.timeout = 120  # 2 min timeout
    
    # Open browser
    webbrowser.open(LOGIN_URL)
    
    print("Waiting for login (2 min timeout)...")
    print("After login, Kite will redirect to 127.0.0.1\n")
    
    # Wait for the redirect
    while TokenCaptureHandler.request_token is None:
        server.handle_request()
    
    server.server_close()
    
    request_token = TokenCaptureHandler.request_token
    print(f"Got request_token: {request_token[:10]}...")
    
    # Exchange for access_token
    print("Exchanging for access_token...")
    result = exchange_token(request_token)
    
    if result.get('status') == 'success':
        access_token = result['data']['access_token']
        print(f"Access token: {access_token[:10]}...")
        save_token(access_token)
        print("\nLogin complete! Iron Pulse can now place orders.")
        return True
    else:
        print(f"ERROR: {result}")
        return False


if __name__ == '__main__':
    login()
