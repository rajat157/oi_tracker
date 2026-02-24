from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/kite", tags=["kite"])


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


@router.get("/callback")
async def kite_callback(request_token: str, request: Request) -> dict:
    """Exchange request_token for access_token."""
    kite_auth = getattr(request.app.state, "kite_auth", None)
    if not kite_auth:
        raise HTTPException(status_code=503, detail="Service not initialized")
    if not request_token:
        raise HTTPException(status_code=400, detail="Missing request_token")
    try:
        access_token = await kite_auth.exchange_token(request_token)
        return {"success": True, "token_prefix": access_token[:10] + "..."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
