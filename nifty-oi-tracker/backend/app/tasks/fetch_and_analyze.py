"""The 3-minute heartbeat task — fetch, analyze, evaluate strategies, publish."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone

from app.core.constants import FORCE_CLOSE_TIME
from app.engine.oi_analyzer import analyze_tug_of_war, calculate_market_trend
from app.schemas.common import StrategyName
from app.services.logging_service import get_logger
from app.services.premium_monitor_service import ActiveTrade

log = get_logger("fetch_task")

IST = timezone(timedelta(hours=5, minutes=30))

# Strategy → is_selling
_STRATEGY_META = {
    StrategyName.IRON_PULSE: False,
    StrategyName.SELLING: True,
    StrategyName.DESSERT: False,
    StrategyName.MOMENTUM: False,
}


async def fetch_and_analyze(services: dict) -> dict | None:
    """
    Main orchestration task run every 3 minutes.

    services dict must contain:
        scheduler, market_data, analysis, trade, alert, event_bus,
        session_factory, premium_monitor, strategies, instruments
    """
    scheduler_svc = services["scheduler"]
    market_data = services["market_data"]
    alert_svc = services["alert"]
    event_bus = services["event_bus"]
    session_factory = services["session_factory"]
    premium_monitor = services["premium_monitor"]
    strategies = services["strategies"]  # {StrategyName: TradingStrategy}
    instruments = services["instruments"]
    shadow_mode = services.get("shadow_mode", False)

    # 1. Market hours check
    if not scheduler_svc.is_market_open():
        log.debug("Market closed, skipping")
        return None

    log.info("Fetching OI data")

    try:
        # 2. Fetch option chain
        parsed = await market_data.fetch_option_chain()
        if not parsed:
            log.error("Failed to fetch option chain")
            return None

        timestamp = datetime.now(IST)
        spot_price = parsed["spot_price"]
        strikes_data = parsed["strikes"]
        current_expiry = parsed["current_expiry"]

        # 3. Fetch VIX + futures
        vix = await market_data.fetch_india_vix() or 0.0
        futures_data = await market_data.fetch_futures_data() or {}
        futures_oi = futures_data.get("future_oi", 0)
        futures_basis = futures_data.get("basis", 0.0)

        async with session_factory() as session:
            from app.services.analysis_service import AnalysisService
            from app.services.trade_service import TradeService

            analysis_svc = AnalysisService(session)
            trade_svc = TradeService(session)

            # 4. Save snapshots
            await analysis_svc.save_snapshots(
                timestamp, spot_price, strikes_data, current_expiry
            )

            # 5. Get previous verdict for hysteresis
            prev_verdict = await analysis_svc.get_prev_verdict()

            # 6. Run OI analysis
            analysis = analyze_tug_of_war(
                strikes_data, spot_price, prev_verdict=prev_verdict, vix=vix,
                futures_oi_change=0,
            )
            analysis["vix"] = vix
            analysis["futures_oi"] = futures_oi
            analysis["futures_basis"] = futures_basis
            analysis["timestamp"] = timestamp.isoformat()

            # 7. Save analysis
            await analysis_svc.save_analysis(analysis, current_expiry)

            # 8. Force close at 15:20 IST
            now_time = timestamp.time()
            if now_time >= FORCE_CLOSE_TIME:
                await _force_close_all(
                    trade_svc, strategies, strikes_data, timestamp, alert_svc, shadow_mode
                )

            # 9. Evaluate strategies
            for strategy_name, strategy in strategies.items():
                try:
                    is_selling = _STRATEGY_META.get(strategy_name, False)
                    active = await trade_svc.get_active_trade(strategy_name)

                    if active:
                        # Check exit
                        opt_key = "ce_ltp" if active["option_type"] == "CE" else "pe_ltp"
                        strike_data = strikes_data.get(active["strike"], {})
                        current_premium = strike_data.get(opt_key, 0)
                        if current_premium <= 0:
                            continue

                        exit_info = strategy.check_exit(active, current_premium, timestamp)
                        if exit_info:
                            if shadow_mode:
                                log.info(
                                    "SHADOW: would exit trade",
                                    strategy=strategy_name.value,
                                    exit_info=str(exit_info),
                                )
                            else:
                                await trade_svc.update_trade(
                                    strategy_name, active["id"], exit_info
                                )
                                premium_monitor.unregister_trade(active["id"])
                                await alert_svc.send_trade_exit_alert(
                                    strategy_name.value, active, exit_info
                                )
                    else:
                        # Check entry
                        if await trade_svc.has_traded_today(strategy_name):
                            continue
                        entry = strategy.should_enter(analysis, strikes_data)
                        if entry:
                            if shadow_mode:
                                log.info(
                                    "SHADOW: would enter trade",
                                    strategy=strategy_name.value,
                                    entry=str(entry),
                                )
                            else:
                                trade_id = await trade_svc.create_trade(strategy_name, entry)
                                await alert_svc.send_trade_entry_alert(
                                    strategy_name.value, entry
                                )
                                # Register with premium monitor
                                token = instruments.get_instrument_token(
                                    entry["strike"], entry["option_type"], current_expiry
                                )
                                if token:
                                    premium_monitor.register_trade(
                                        ActiveTrade(
                                            trade_id=trade_id,
                                            strategy=strategy_name.value,
                                            strike=entry["strike"],
                                            option_type=entry["option_type"],
                                            instrument_token=token,
                                            entry_premium=entry["entry_premium"],
                                            sl_premium=entry["sl_premium"],
                                            target_premium=entry.get(
                                                "target1_premium",
                                                entry.get("target_premium", 0),
                                            ),
                                            is_selling=is_selling,
                                        )
                                    )
                except Exception as e:
                    log.error(
                        "Strategy error",
                        strategy=strategy_name.value,
                        error=str(e),
                    )

            await session.commit()

        # 10. Publish SSE event
        from app.core.events import SSEEvent

        await event_bus.publish(
            SSEEvent(event="analysis_update", data=json.dumps(analysis, default=str))
        )

        log.info("Cycle complete", verdict=analysis.get("verdict", "?"))
        return analysis

    except Exception as e:
        log.error("Error in fetch_and_analyze", error=str(e))
        return None


async def _force_close_all(
    trade_svc, strategies, strikes_data, now, alert_svc, shadow_mode=False
) -> None:
    """Force close all active trades at EOD."""
    for strategy_name, strategy in strategies.items():
        active = await trade_svc.get_active_trade(strategy_name)
        if not active:
            continue
        if not strategy.should_force_close(active, now):
            continue

        opt_key = "ce_ltp" if active["option_type"] == "CE" else "pe_ltp"
        strike_data = strikes_data.get(active["strike"], {})
        current_premium = strike_data.get(opt_key, 0)

        is_selling = _STRATEGY_META.get(strategy_name, False)
        pnl = strategy.compute_pnl_pct(active["entry_premium"], current_premium, is_selling)

        updates = {
            "status": "WON" if pnl > 0 else "LOST",
            "exit_premium": current_premium,
            "exit_reason": "EOD force close",
            "profit_loss_pct": pnl,
            "resolved_at": now,
        }
        if shadow_mode:
            log.info(
                "SHADOW: would force close trade",
                strategy=strategy_name.value,
                updates=str(updates),
            )
        else:
            await trade_svc.update_trade(strategy_name, active["id"], updates)
            await alert_svc.send_trade_exit_alert(strategy_name.value, active, updates)
