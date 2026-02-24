"""Scheduler service — 3-minute polling candle-aligned to market hours."""

from __future__ import annotations

from datetime import datetime, time, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.constants import MARKET_CLOSE, MARKET_OPEN
from app.services.logging_service import get_logger

log = get_logger("scheduler")

# IST offset
IST = timezone(timedelta(hours=5, minutes=30))


class SchedulerService:
    def __init__(self) -> None:
        self._scheduler: AsyncIOScheduler | None = None
        self._job_func = None
        self._running = False

    def is_market_open(self) -> bool:
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return False
        return MARKET_OPEN <= now.time() <= MARKET_CLOSE

    def start(self, job_func, interval_minutes: int = 3) -> None:
        """Start the scheduler with candle-aligned intervals."""
        self._job_func = job_func
        self._scheduler = AsyncIOScheduler()

        # Calculate next candle-aligned time
        now = datetime.now(IST)
        minute = now.minute
        next_candle_minute = ((minute // interval_minutes) + 1) * interval_minutes
        if next_candle_minute >= 60:
            next_run = now.replace(
                minute=0, second=0, microsecond=0
            ) + timedelta(hours=1)
        else:
            next_run = now.replace(
                minute=next_candle_minute, second=0, microsecond=0
            )

        self._scheduler.add_job(
            self._job_func,
            trigger=IntervalTrigger(minutes=interval_minutes, start_date=next_run),
            id="fetch_and_analyze",
            replace_existing=True,
        )
        self._scheduler.start()
        self._running = True
        log.info(
            "Scheduler started",
            interval=f"{interval_minutes}m",
            next_run=next_run.strftime("%H:%M:%S"),
        )

    def stop(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        self._running = False
        log.info("Scheduler stopped")

    async def trigger_now(self) -> None:
        """Manually trigger the job."""
        if self._job_func:
            await self._job_func()
        else:
            log.warning("No job function registered")

    @property
    def running(self) -> bool:
        return self._running
