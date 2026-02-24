from fastapi import APIRouter

router = APIRouter(prefix="/kite", tags=["kite"])


@router.get("/status")
async def kite_status() -> dict:
    """Check Kite authentication status."""
    return {"authenticated": False, "message": "Not configured yet"}


@router.get("/login")
async def kite_login() -> dict:
    """Redirect to Kite OAuth login."""
    return {"message": "Not implemented yet"}


@router.get("/callback")
async def kite_callback(request_token: str = "") -> dict:
    """Exchange request_token for access_token."""
    return {"message": "Not implemented yet"}
