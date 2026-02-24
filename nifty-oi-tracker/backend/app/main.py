from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from functools import partial

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.dependencies import _event_bus


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Initialize all services on startup, clean up on shutdown."""
    from app.core.database import get_session_factory
    from app.services.alert_service import AlertService
    from app.services.instrument_service import InstrumentService
    from app.services.kite_auth_service import KiteAuthService
    from app.services.logging_service import configure_logging, get_logger
    from app.services.market_data_service import MarketDataService
    from app.services.premium_monitor_service import PremiumMonitorService
    from app.services.scheduler_service import SchedulerService
    from app.tasks.fetch_and_analyze import fetch_and_analyze

    log = get_logger("app")

    # 1. Logging
    session_factory = get_session_factory()
    configure_logging(session_factory=session_factory, min_level=settings.log_level)

    # 2. Services
    kite_auth = KiteAuthService(session_factory=session_factory)
    alert_svc = AlertService()
    instruments = InstrumentService(api_key=settings.kite_api_key)
    market_data = MarketDataService(kite_auth=kite_auth, instruments=instruments)
    premium_monitor = PremiumMonitorService(shadow_mode=False)
    scheduler_svc = SchedulerService()

    # Build strategies map
    from app.schemas.common import StrategyName
    from app.strategies.iron_pulse import IronPulseStrategy
    from app.strategies.selling import SellingStrategy
    from app.strategies.dessert import DessertStrategy
    from app.strategies.momentum import MomentumStrategy

    strategies = {
        StrategyName.IRON_PULSE: IronPulseStrategy(),
        StrategyName.SELLING: SellingStrategy(),
        StrategyName.DESSERT: DessertStrategy(),
        StrategyName.MOMENTUM: MomentumStrategy(),
    }

    # Wire services dict for the task
    services = {
        "scheduler": scheduler_svc,
        "market_data": market_data,
        "alert": alert_svc,
        "event_bus": _event_bus,
        "session_factory": session_factory,
        "premium_monitor": premium_monitor,
        "instruments": instruments,
        "strategies": strategies,
    }

    # Premium monitor exit callback
    async def handle_premium_exit(exit_info):
        async with session_factory() as session:
            from app.services.trade_service import TradeService
            trade_svc = TradeService(session)
            strategy_name = StrategyName(exit_info["strategy"])
            await trade_svc.update_trade(strategy_name, exit_info["trade_id"], {
                "status": exit_info["action"],
                "exit_premium": exit_info["exit_premium"],
                "exit_reason": exit_info["reason"],
                "profit_loss_pct": exit_info["pnl_pct"],
            })
            await session.commit()
        premium_monitor.unregister_trade(exit_info["trade_id"])
        active = {"strike": 0, "option_type": "CE", "entry_premium": 0}
        await alert_svc.send_trade_exit_alert(exit_info["strategy"], active, exit_info)

    premium_monitor.set_exit_callback(lambda r: handle_premium_exit(r))

    # Store on app.state for dependency injection
    app.state.kite_auth = kite_auth
    app.state.alert = alert_svc
    app.state.scheduler = scheduler_svc
    app.state.session_factory = session_factory
    app.state.services = services

    # Start scheduler
    job = partial(fetch_and_analyze, services)
    scheduler_svc.start(job, interval_minutes=3)

    # Start premium monitor if authenticated
    if await kite_auth.is_authenticated():
        token = await kite_auth.get_access_token()
        premium_monitor.start(settings.kite_api_key, token)

    log.info("App started")
    yield

    # Shutdown
    scheduler_svc.stop()
    premium_monitor.stop()
    await alert_svc.close()
    log.info("App stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="NIFTY OI Tracker",
        version="2.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_v1_router, prefix="/api/v1")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": "2.0.0"}

    return app


app = create_app()
