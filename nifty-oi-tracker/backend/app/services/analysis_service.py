"""Orchestrates the OI analysis pipeline and stores results."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import AnalysisHistory
from app.models.oi_snapshot import OISnapshot


class AnalysisService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_snapshots(
        self,
        timestamp: datetime,
        spot_price: float,
        strikes_data: dict,
        expiry_date: str,
    ) -> None:
        """Bulk-insert OI snapshots for all strikes."""
        for strike, data in strikes_data.items():
            snapshot = OISnapshot(
                timestamp=timestamp,
                spot_price=spot_price,
                strike_price=strike,
                ce_oi=data.get("ce_oi", 0),
                ce_oi_change=data.get("ce_oi_change", 0),
                pe_oi=data.get("pe_oi", 0),
                pe_oi_change=data.get("pe_oi_change", 0),
                ce_volume=data.get("ce_volume", 0),
                pe_volume=data.get("pe_volume", 0),
                ce_iv=data.get("ce_iv", 0),
                pe_iv=data.get("pe_iv", 0),
                ce_ltp=data.get("ce_ltp", 0),
                pe_ltp=data.get("pe_ltp", 0),
                expiry_date=expiry_date,
            )
            self.session.add(snapshot)
        await self.session.flush()

    async def save_analysis(self, analysis: dict, expiry_date: str) -> int:
        """Save analysis result to DB. Returns the new row id."""
        record = AnalysisHistory(
            timestamp=datetime.now(timezone.utc),
            spot_price=analysis.get("spot_price", 0),
            atm_strike=analysis.get("atm_strike", 0),
            total_call_oi=analysis.get("total_call_oi", 0),
            total_put_oi=analysis.get("total_put_oi", 0),
            call_oi_change=analysis.get("call_oi_change", 0),
            put_oi_change=analysis.get("put_oi_change", 0),
            atm_call_oi_change=analysis.get("atm_call_oi_change", 0),
            atm_put_oi_change=analysis.get("atm_put_oi_change", 0),
            itm_call_oi_change=analysis.get("itm_call_oi_change", 0),
            itm_put_oi_change=analysis.get("itm_put_oi_change", 0),
            verdict=analysis.get("verdict", "No Data"),
            prev_verdict=analysis.get("prev_verdict"),
            expiry_date=expiry_date,
            vix=analysis.get("vix", 0),
            iv_skew=analysis.get("iv_skew", 0),
            max_pain=analysis.get("max_pain", 0),
            signal_confidence=analysis.get("signal_confidence", 0),
            futures_oi=analysis.get("futures_oi", 0),
            futures_oi_change=analysis.get("futures_oi_change", 0),
            futures_basis=analysis.get("futures_basis", 0),
            analysis_blob=analysis,
        )
        self.session.add(record)
        await self.session.flush()
        return record.id

    async def get_latest(self) -> dict | None:
        """Get the most recent analysis record."""
        stmt = select(AnalysisHistory).order_by(AnalysisHistory.id.desc()).limit(1)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if not row:
            return None
        return {c.name: getattr(row, c.name) for c in row.__table__.columns}

    async def get_history(self, limit: int = 100) -> list[dict]:
        """Get recent analysis history for charts."""
        stmt = (
            select(AnalysisHistory)
            .order_by(AnalysisHistory.id.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        rows = result.scalars().all()
        return [
            {
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "spot_price": r.spot_price,
                "verdict": r.verdict,
                "signal_confidence": r.signal_confidence,
                "vix": r.vix,
            }
            for r in reversed(rows)  # Oldest first for charts
        ]

    async def get_prev_verdict(self) -> str | None:
        """Get the previous analysis verdict for hysteresis."""
        stmt = (
            select(AnalysisHistory.verdict)
            .order_by(AnalysisHistory.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return row
