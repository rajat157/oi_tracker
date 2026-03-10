"""Kite Connect authentication routes."""

from flask import Blueprint, jsonify, request, redirect

bp = Blueprint("kite_auth", __name__)


@bp.route("/kite/login")
def kite_login():
    """Redirect to Kite login page."""
    import os
    api_key = os.environ.get("KITE_API_KEY", "")
    if not api_key:
        return jsonify({"error": "KITE_API_KEY not configured"}), 400
    return redirect(f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}")


@bp.route("/kite/callback")
def kite_callback():
    """Capture request_token from Kite redirect and exchange for access_token."""
    import os, hashlib, requests as req

    request_token = request.args.get("request_token", "")
    if not request_token:
        return _error_page("Login Failed", "No request token received from Kite.")

    api_key = os.environ.get("KITE_API_KEY", "")
    api_secret = os.environ.get("KITE_API_SECRET", "")

    if not api_secret:
        return _error_page("API Secret Missing",
                           f"Add KITE_API_SECRET to .env. Token: <code>{request_token}</code>",
                           color="orange")

    checksum = hashlib.sha256(
        f"{api_key}{request_token}{api_secret}".encode()
    ).hexdigest()

    try:
        resp = req.post("https://api.kite.trade/session/token", data={
            "api_key": api_key, "request_token": request_token, "checksum": checksum,
        }, timeout=10)
        result = resp.json()

        if result.get("status") == "success":
            access_token = result["data"]["access_token"]
            from kite.auth import save_token
            save_token(access_token)
            return _success_page(access_token)
        else:
            return _error_page("Token Exchange Failed",
                               result.get("message", "Unknown error"))
    except Exception as e:
        return _error_page("Error", str(e))


@bp.route("/kite/status")
def kite_status():
    """Check if Kite is authenticated for today."""
    from kite.auth import load_token
    token = load_token()
    return jsonify({
        "authenticated": bool(token),
        "token_preview": f"{token[:10]}..." if token else None,
    })


@bp.route("/kite/save-token", methods=["POST"])
def kite_save_token():
    """Manually save an access token."""
    data = request.get_json()
    token = data.get("token", "").strip()
    if not token:
        return jsonify({"error": "No token provided"}), 400
    from kite.auth import save_token
    save_token(token)
    return jsonify({"status": "success", "message": "Token saved"})


def _error_page(title: str, msg: str, color: str = "red") -> str:
    return (f'<html><body style="font-family:sans-serif;text-align:center;padding:50px;">'
            f'<h1 style="color:{color};">{title}</h1><p>{msg}</p>'
            f'<a href="/kite/login">Try Again</a></body></html>')


def _success_page(token: str) -> str:
    return (f'<html><body style="font-family:sans-serif;text-align:center;padding:50px;">'
            f'<h1 style="color:green;">Login Successful!</h1>'
            f'<p>Access token saved.</p><p>Token: <code>{token[:10]}...</code></p>'
            f'<p><a href="/">Back to Dashboard</a></p>'
            f'<script>setTimeout(function(){{ window.close(); }}, 3000);</script>'
            f'</body></html>')
