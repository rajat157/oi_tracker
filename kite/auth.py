"""
Kite Connect OAuth login flow.
Run this once each morning to get a daily access token.

Usage: python kite_auth.py
Opens browser -> login -> captures token -> stores in .env
"""
import os
import time
import hashlib
import webbrowser
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv, set_key
from datetime import datetime

from core.logger import get_logger

log = get_logger("kite_auth")

load_dotenv()

API_KEY = os.environ.get('KITE_API_KEY', '')
API_SECRET = os.environ.get('KITE_API_SECRET', '')
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')

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
    """Save access token to database and .env."""
    from db.settings_repo import set_setting
    set_setting('kite_access_token', access_token)
    set_setting('kite_token_date', datetime.now().strftime('%Y-%m-%d'))

    # Also save to .env as backup
    set_key(ENV_PATH, 'KITE_ACCESS_TOKEN', access_token)
    print(f"Access token saved to database and .env")


def load_token() -> str:
    """Load today's access token from database."""
    from db.settings_repo import get_setting
    token_date = get_setting('kite_token_date')
    today = datetime.now().strftime('%Y-%m-%d')

    if token_date == today:
        token = get_setting('kite_access_token')
        return token or ""
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


def ensure_authenticated(
    timeout_seconds: int = 240,
    poll_interval: float = 2.0,
    auto_open_browser: bool = True,
) -> bool:
    """Block until a valid Kite access token exists for today.

    Used at app startup BEFORE any Kite API call. The flow is:
        1. If today's token is already in the DB, return immediately.
        2. Otherwise, open the Kite login URL in the user's default browser.
        3. The user logs in; Kite redirects to the dev-console-configured
           callback URL (we assume http://127.0.0.1:5000/kite/callback,
           which the Flask blueprint at api/kite_auth.py handles).
        4. Poll the DB until the callback writes the token.

    NOTE: For this to work, the Kite developer console for your API key
    MUST have its redirect URL set to http://127.0.0.1:5000/kite/callback
    (or whatever Flask is bound to).

    Args:
        timeout_seconds: Maximum total wait time before giving up.
        poll_interval: How often to check the DB for a saved token.
        auto_open_browser: Open the login URL in the default browser.

    Returns:
        True if a valid token is in the DB by the time we return.
    """
    if load_token():
        log.info("Kite already authenticated for today")
        return True

    if not API_KEY:
        log.error("KITE_API_KEY not set in .env")
        return False

    login_url = LOGIN_URL
    log.warning("=" * 60)
    log.warning("KITE AUTHENTICATION REQUIRED")
    log.warning("Opening browser to Kite login page")
    log.warning(f"URL: {login_url}")
    log.warning("After login, the /kite/callback Flask route will save")
    log.warning("the token to the DB. This thread will block until then.")
    log.warning(f"Timeout: {timeout_seconds}s")
    log.warning("=" * 60)

    if auto_open_browser:
        try:
            webbrowser.open(login_url)
        except Exception as e:
            log.warning("Could not auto-open browser", error=str(e))
            log.warning(f"Open this URL manually: {login_url}")

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if load_token():
            log.info("Kite token captured via callback")
            return True
        time.sleep(poll_interval)

    log.error("Kite authentication timed out — no token captured")
    log.error(f"You can complete login manually by visiting: {login_url}")
    return False


if __name__ == '__main__':
    login()
