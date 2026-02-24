"""Unified trade CRUD across all strategy tables."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trade import (
    DessertTrade,
    IronPulseTrade,
    MomentumTrade,
    SellingTrade,
)
from app.schemas.common import StrategyName

_MODEL_MAP = {
    StrategyName.IRON_PULSE: IronPulseTrade,
    StrategyName.SELLING: SellingTrade,
    StrategyName.DESSERT: DessertTrade,
    StrategyName.MOMENTUM: MomentumTrade,
}


class TradeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _model(self, strategy: StrategyName):
        return _MODEL_MAP[strategy]

    # ── Read ──────────────────────────────────────────────

    async def get_active_trade(self, strategy: StrategyName) -> dict | None:
        model = self._model(strategy)
        stmt = (
            select(model)
            .where(model.status.in_(["ACTIVE", "PENDING"]))
            .order_by(model.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return _row_to_dict(row) if row else None

    async def has_traded_today(self, strategy: StrategyName) -> bool:
        model = self._model(strategy)
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        stmt = select(func.count()).select_from(model).where(model.created_at >= today_start)
        result = await self.session.execute(stmt)
        return (result.scalar() or 0) > 0

    async def get_trades(
        self,
        strategy: StrategyName,
        days: int = 30,
        status: str | None = None,
        direction: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        model = self._model(strategy)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        conditions = [model.created_at >= cutoff]
        if status:
            conditions.append(model.status == status)
        if direction:
            conditions.append(model.direction == direction)

        # Count
        count_stmt = select(func.count()).select_from(model).where(and_(*conditions))
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar() or 0

        # Data
        data_stmt = (
            select(model)
            .where(and_(*conditions))
            .order_by(model.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        data_result = await self.session.execute(data_stmt)
        rows = data_result.scalars().all()

        return [_row_to_dict(r) for r in rows], total

    async def get_stats(self, strategy: StrategyName, days: int = 30) -> dict:
        model = self._model(strategy)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        resolved = [model.created_at >= cutoff, model.status.in_(["WON", "LOST"])]

        stmt = select(
            func.count().label("total"),
            func.sum(func.cast(model.status == "WON", model.__table__.c.id.type)).label("won"),
            func.avg(model.profit_loss_pct).label("avg_pnl"),
            func.sum(model.profit_loss_pct).label("total_pnl"),
        ).where(and_(*resolved))

        result = await self.session.execute(stmt)
        row = result.one()
        total = row.total or 0
        won = row.won or 0
        lost = total - won

        return {
            "total": total,
            "won": won,
            "lost": lost,
            "win_rate": (won / total * 100) if total > 0 else 0,
            "avg_pnl": float(row.avg_pnl or 0),
            "total_pnl": float(row.total_pnl or 0),
        }

    # ── Write ─────────────────────────────────────────────

    async def create_trade(self, strategy: StrategyName, data: dict) -> int:
        model = self._model(strategy)
        trade = model(**data)
        self.session.add(trade)
        await self.session.flush()
        return trade.id

    async def update_trade(self, strategy: StrategyName, trade_id: int, updates: dict) -> None:
        model = self._model(strategy)
        # Remove internal keys (prefixed with _)
        clean = {k: v for k, v in updates.items() if not k.startswith("_")}
        stmt = select(model).where(model.id == trade_id)
        result = await self.session.execute(stmt)
        trade = result.scalar_one_or_none()
        if trade:
            for k, v in clean.items():
                setattr(trade, k, v)
            await self.session.flush()


def _row_to_dict(row) -> dict:
    """Convert SQLAlchemy model instance to dict."""
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}
