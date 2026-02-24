"""Tests for SchedulerService."""

from datetime import datetime, time, timezone, timedelta
from unittest.mock import patch

import pytest

from app.services.scheduler_service import SchedulerService, IST


class TestSchedulerService:
    def test_is_market_open_weekday_market_hours(self):
        svc = SchedulerService()
        # Monday 10:00 IST
        dt = datetime(2026, 2, 23, 10, 0, tzinfo=IST)
        with patch("app.services.scheduler_service.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            assert svc.is_market_open() is True

    def test_is_market_open_weekend(self):
        svc = SchedulerService()
        # Saturday 10:00 IST
        dt = datetime(2026, 2, 28, 10, 0, tzinfo=IST)
        with patch("app.services.scheduler_service.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            assert svc.is_market_open() is False

    def test_is_market_open_before_market(self):
        svc = SchedulerService()
        # Monday 8:00 IST
        dt = datetime(2026, 2, 23, 8, 0, tzinfo=IST)
        with patch("app.services.scheduler_service.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            assert svc.is_market_open() is False

    def test_is_market_open_after_market(self):
        svc = SchedulerService()
        # Monday 16:00 IST
        dt = datetime(2026, 2, 23, 16, 0, tzinfo=IST)
        with patch("app.services.scheduler_service.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            assert svc.is_market_open() is False

    def test_running_property(self):
        svc = SchedulerService()
        assert svc.running is False

    @pytest.mark.asyncio
    async def test_trigger_now_no_func(self):
        svc = SchedulerService()
        await svc.trigger_now()  # Should not raise

    @pytest.mark.asyncio
    async def test_trigger_now_calls_func(self):
        svc = SchedulerService()
        called = []

        async def job():
            called.append(True)

        svc._job_func = job
        await svc.trigger_now()
        assert len(called) == 1

    def test_stop_without_start(self):
        svc = SchedulerService()
        svc.stop()  # Should not raise
        assert svc.running is False
