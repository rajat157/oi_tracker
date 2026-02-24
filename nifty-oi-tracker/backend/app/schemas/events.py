from datetime import datetime

from pydantic import BaseModel


class SSEEventSchema(BaseModel):
    event: str  # analysis_update, trade_update, market_status
    data: dict
    timestamp: datetime
