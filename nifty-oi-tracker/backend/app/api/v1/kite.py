import logging
import traceback

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kite", tags=["kite"])


class TokenPayload(BaseModel):
    access_token: str


@router.get("/status")
async def kite_status(request: Request) -> dict:
    """Check Kite authentication status."""
    kite_auth = getattr(request.app.state, "kite_auth", None)
    if not kite_auth:
        return {"authenticated": False, "message": "Service not initialized"}
    authenticated = await kite_auth.is_authenticated()
    return {"authenticated": authenticated}


@router.get("/login")
async def kite_login(request: Request) -> dict:
    """Return Kite OAuth login URL."""
    kite_auth = getattr(request.app.state, "kite_auth", None)
    if not kite_auth:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return {"login_url": kite_auth.get_login_url()}


@router.post("/token")
async def save_token(body: TokenPayload, request: Request) -> dict:
    """Validate and save a pasted access token."""
    kite_auth = getattr(request.app.state, "kite_auth", None)
    if not kite_auth:
        raise HTTPException(status_code=503, detail="Service not initialized")

    token = body.access_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Access token is empty")

    # Validate by calling kite.profile()
    try:
        profile = await kite_auth.validate_token(token)
    except Exception as e:
        logger.warning("Token validation failed: %s", e)
        raise HTTPException(status_code=400, detail=f"Invalid token: {e}")

    # Save to DB
    await kite_auth.save_access_token(token)

    # Start premium monitor if available
    services = getattr(request.app.state, "services", None)
    if services and "premium_monitor" in services:
        pm = services["premium_monitor"]
        pm.stop()
        pm.start(settings.kite_api_key, token)
        logger.info("Premium monitor (re)started after token save")

    logger.info("Access token saved for user: %s", profile.get("user_name", "unknown"))
    return {"success": True, "user_name": profile.get("user_name", "")}


@router.get("/callback")
async def kite_callback(request_token: str, request: Request) -> dict:
    """Exchange request_token for access_token (OAuth redirect flow)."""
    kite_auth = getattr(request.app.state, "kite_auth", None)
    if not kite_auth:
        raise HTTPException(status_code=503, detail="Service not initialized")
    if not request_token:
        raise HTTPException(status_code=400, detail="Missing request_token")
    try:
        access_token = await kite_auth.exchange_token(request_token)
        return {"success": True, "token_prefix": access_token[:10] + "..."}
    except Exception as e:
        logger.error("Token exchange failed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {e}")
