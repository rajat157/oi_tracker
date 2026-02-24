from datetime import datetime

from pydantic import BaseModel


class MarketStatus(BaseModel):
    is_open: bool
    market_open: str
    market_close: str
    server_time: datetime


class RefreshResponse(BaseModel):
    message: str
    triggered_at: datetime
