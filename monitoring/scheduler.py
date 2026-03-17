"""
APScheduler for periodic OI data fetching
Runs every 3 minutes to match NSE update frequency
Only runs during market hours (9:15 AM - 3:30 PM IST, weekdays)
"""

import json
from datetime import datetime, time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from kite.data import KiteDataFetcher
from monitoring.premium_monitor import PremiumMonitor, ActiveTrade
from analysis.tug_of_war import analyze_tug_of_war, calculate_market_trend
from db.legacy import (
    save_snapshot, save_analysis, purge_old_data, get_last_data_date,
    get_recent_price_trend, get_recent_oi_changes, get_previous_strikes_data,
    get_previous_futures_oi, get_analysis_history, get_previous_verdict,
    save_orderflow_depth, purge_old_orderflow,
    get_previous_smoothed_score
)
from db.trade_repo import TradeRepository
from strategies.scalper import ScalperStrategy
from strategies.rr_strategy import RRStrategy
from kite.order_executor import OrderExecutor
from alerts.broker import AlertBroker
from analysis.v_shape import VShapeDetector
from analysis.pattern_tracker import check_patterns, log_failed_entry
from core.logger import get_logger

log = get_logger("scheduler")


# Market timing constants (IST)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


class OIScheduler:
    """Manages periodic fetching and analysis of OI data."""

    def __init__(self, socketio=None, headless: bool = True):
        """
        Initialize the scheduler.

        Args:
            socketio: Flask-SocketIO instance for pushing updates
            headless: Run browser in headless mode (unused — kept for API compat)
        """
        self.scheduler = BackgroundScheduler()
        self.headless = headless
        self.socketio = socketio
        self.last_analysis = None
        self.is_running = False
        self.last_purge_date = None
        self.last_learning_date = None
        self.order_executor = OrderExecutor()
        repo = TradeRepository()
        self.strategies = {
            "scalper": ScalperStrategy(trade_repo=repo, order_executor=self.order_executor),
            "rally_rider": RRStrategy(trade_repo=repo, order_executor=self.order_executor),
        }
        self._alert_broker = AlertBroker()
        self.v_shape_detector = VShapeDetector()
        self.force_enabled = False
        # Kite data fetcher (reusable, no browser)
        self.kite_fetcher = KiteDataFetcher()
        # Premium monitor for real-time SL/target detection
        self.premium_monitor = PremiumMonitor(socketio=socketio, shadow_mode=False)
        self.premium_monitor.set_exit_callback(self._handle_premium_exit)

    def set_force_enabled(self, enabled: bool):
        """Enable/disable force fetch mode for automatic polling."""
        self.force_enabled = enabled

    def _register_trade_with_monitor(self, strategy):
        """Register a newly created/activated trade with the WebSocket premium monitor."""
        try:
            setup = strategy.get_active()
            if not setup:
                log.warning("WS registration skipped: no active trade found", tracker=strategy.tracker_type)
                return
            if not self.premium_monitor._instrument_map:
                log.warning("WS registration skipped: no instrument map", tracker=strategy.tracker_type)
                return
            expiry = self.premium_monitor._instrument_map.get_current_expiry()
            if not expiry:
                log.warning("WS registration skipped: no current expiry", tracker=strategy.tracker_type)
                return
            trade_obj = self.premium_monitor._db_trade_to_active(
                setup, strategy.tracker_type, expiry, is_selling=strategy.is_selling)
            if not trade_obj:
                log.warning("WS registration skipped: instrument token not found",
                            tracker=strategy.tracker_type, strike=setup.get('strike'), type=setup.get('option_type'))
                return
            self.premium_monitor.register_trade(trade_obj)
            log.info("Trade registered with WebSocket monitor",
                     tracker=strategy.tracker_type, trade_id=setup['id'],
                     strike=setup.get('strike'), type=setup.get('option_type'))
        except Exception as e:
            log.error("WS registration failed", tracker=strategy.tracker_type, error=str(e))

    def is_market_open(self) -> bool:
        """Check if the market is currently open."""
        now = datetime.now()

        # Check if it's a weekday (Monday=0, Sunday=6)
        if now.weekday() >= 5:  # Saturday or Sunday
            return False

        # Check if current time is within market hours
        current_time = now.time()
        return MARKET_OPEN <= current_time <= MARKET_CLOSE

    def check_and_purge_old_data(self):
        """Purge data older than 90 days to enable historical analysis and self-learning."""
        from datetime import timedelta
        today = datetime.now().date()

        # Only check once per day
        if self.last_purge_date == today:
            return

        # Keep 90 days of data for historical analysis and self-learning
        cutoff_date = today - timedelta(days=90)

        # Check if there's old data to purge
        last_data_date = get_last_data_date()
        if last_data_date and last_data_date < cutoff_date:
            log.info("Purging old data", cutoff=str(cutoff_date), retention_days=90)
            purge_old_data(cutoff_date)
            log.info("Old data purged", keep_from=str(cutoff_date))

        # Purge orderflow depth data (30-day rolling window)
        purge_old_orderflow(days=30)

        self.last_purge_date = today

    def fetch_and_analyze(self):
        """
        Main job: Fetch data, analyze, save, and broadcast.
        Only runs during market hours (9:15 AM - 3:30 PM IST, weekdays).
        """
        # Check and purge old data at start of new day
        self.check_and_purge_old_data()

        # Hard guard: NEVER fetch outside market hours
        if not self.is_market_open():
            log.debug("Market is closed, skipping fetch")
            return

        log.info("Fetching OI data")

        try:
            # Fetch via Kite API (already parsed, no browser needed)
            parsed = self.kite_fetcher.fetch_option_chain()
            if not parsed:
                log.error("Failed to fetch data from Kite")
                return

            timestamp = datetime.now()
            spot_price = parsed["spot_price"]
            strikes_data = parsed["strikes"]
            current_expiry = parsed["current_expiry"]

            # Save snapshot to database
            save_snapshot(timestamp, spot_price, strikes_data, current_expiry)

            # Update core strike subscriptions for orderflow collection
            self.premium_monitor.update_core_strikes(spot_price)

            # Get price history for momentum calculation
            price_history = get_recent_price_trend(lookback_minutes=9)

            # Get previous OI changes for acceleration calculation (3 points = 9 min)
            prev_oi_changes = get_recent_oi_changes(lookback=3)

            # Get previous strikes data for premium momentum
            prev_strikes_data = get_previous_strikes_data()

            # Fetch India VIX for volatility context
            vix = self.kite_fetcher.fetch_india_vix() or 0.0
            if vix > 0:
                log.info("India VIX fetched", vix=f"{vix:.2f}")
            else:
                log.debug("VIX not available")

            # Fetch futures data for cross-validation
            futures_data = self.kite_fetcher.fetch_futures_data() or {}
            futures_oi = futures_data.get("future_oi", 0)
            futures_basis = futures_data.get("basis", 0.0)

            # Calculate futures OI change from previous stored value
            prev_futures_oi = get_previous_futures_oi()
            if prev_futures_oi > 0 and futures_oi > 0:
                futures_oi_change = futures_oi - prev_futures_oi
            else:
                futures_oi_change = 0

            if futures_oi > 0:
                log.info("Futures OI", change=f"{futures_oi_change:+,}", prev=f"{prev_futures_oi:,}", curr=f"{futures_oi:,}")

            # Get previous verdict (today only — no overnight carryover)
            prev_verdict = get_previous_verdict()
            prev_smoothed_score = get_previous_smoothed_score()

            # Perform analysis with all enhanced data
            analysis = analyze_tug_of_war(
                strikes_data,
                spot_price,
                price_history=price_history,
                vix=vix,
                futures_oi_change=futures_oi_change,
                prev_oi_changes=prev_oi_changes,
                prev_strikes_data=prev_strikes_data,
                prev_verdict=prev_verdict,
                prev_smoothed_score=prev_smoothed_score
            )
            analysis["timestamp"] = timestamp.isoformat()
            analysis["expiry_date"] = current_expiry

            # Self-learning removed - using fixed strategy params
            analysis["self_learning"] = {
                "should_trade": True,
                "is_paused": False,
                "ema_accuracy": 0,
                "consecutive_errors": 0
            }

            # Calculate market trend from recent analysis history (today only)
            from datetime import date as date_cls
            today_str = date_cls.today().strftime("%Y-%m-%d")
            trend_history = get_analysis_history(limit=15, date=today_str)
            market_trend = calculate_market_trend(trend_history, lookback=10)
            analysis["market_trend"] = market_trend

            # Display-only: PCR trend
            from db.legacy import get_recent_pcr_values, get_recent_max_pain_values
            from analysis.tug_of_war import calculate_pcr_trend, calculate_max_pain_drift
            pcr_history = get_recent_pcr_values(limit=10)
            analysis["pcr_trend"] = calculate_pcr_trend(pcr_history)

            # Display-only: Max pain drift
            mp_history = get_recent_max_pain_values(limit=20)
            analysis["max_pain_drift"] = calculate_max_pain_drift(
                mp_history, analysis.get("max_pain", 0), spot_price
            )

            # Add futures data to analysis for frontend
            analysis["futures_oi"] = futures_oi
            analysis["futures_basis"] = round(futures_basis, 2) if futures_basis else 0.0
            analysis["futures_price"] = round(spot_price + (futures_basis or 0), 2)
            analysis["futures_oi_change"] = futures_oi_change

            # Serialize complete analysis to JSON for storage (now includes self_learning)
            analysis_json = json.dumps(analysis, default=str)

            # Save analysis to database with full JSON blob
            save_analysis(
                timestamp=timestamp,
                spot_price=spot_price,
                atm_strike=analysis["atm_strike"],
                total_call_oi=analysis["total_call_oi"],
                total_put_oi=analysis["total_put_oi"],
                call_oi_change=analysis["call_oi_change"],
                put_oi_change=analysis["put_oi_change"],
                verdict=analysis["verdict"],
                expiry_date=current_expiry,
                atm_call_oi_change=analysis.get("atm_data", {}).get("call_oi_change", 0) if analysis.get("atm_data") else 0,
                atm_put_oi_change=analysis.get("atm_data", {}).get("put_oi_change", 0) if analysis.get("atm_data") else 0,
                itm_call_oi_change=analysis.get("itm_call_oi_change", 0),
                itm_put_oi_change=analysis.get("itm_put_oi_change", 0),
                vix=vix,
                iv_skew=analysis.get("iv_skew", 0.0),
                max_pain=analysis.get("max_pain", 0),
                signal_confidence=analysis.get("signal_confidence", 0.0),
                futures_oi=futures_oi,
                futures_oi_change=futures_oi_change,
                futures_basis=futures_basis,
                analysis_json=analysis_json,  # Store complete analysis
                prev_verdict=prev_verdict  # Store previous verdict for hysteresis analysis
            )

            self.last_analysis = analysis

            log.info("Analysis complete", verdict=analysis['verdict'])

            # Pattern Tracker: detect patterns (PM reversal alerts disabled)
            try:
                check_patterns(analysis)
                # PM reversal alerts disabled - using new strategy instead
                analysis["call_alert"] = None
            except Exception as e:
                log.error("Error in pattern tracking", error=str(e))
                analysis["call_alert"] = None

            # V-Shape Detector: detect intraday V-shape recovery setups
            try:
                v_shape_result = self.v_shape_detector.evaluate(
                    analysis, futures_basis=futures_basis,
                    futures_oi=futures_oi, futures_oi_change=futures_oi_change
                )
                if v_shape_result:
                    analysis["v_shape"] = v_shape_result
                    log.info("V-shape signal", level=v_shape_result["signal_level"])
                else:
                    from analysis.v_shape import get_v_shape_status
                    analysis["v_shape"] = get_v_shape_status()
            except Exception as e:
                log.error("V-shape detector error", error=str(e))
                analysis["v_shape"] = None

            # ===== SCALPER AGENT (Claude-powered premium chart analysis) =====
            scalper = self.strategies["scalper"]
            try:
                # Check/update active scalp trade
                scalp_update = scalper.check_and_update(
                        strikes_data, analysis=analysis,
                        premium_monitor=self.premium_monitor)
                if scalp_update:
                    log.info("Scalp trade updated",
                             action=scalp_update['action'],
                             pnl=f"{scalp_update['pnl']:.2f}%",
                             reason=scalp_update['reason'])
                    if scalp_update['action'] in ('WON', 'LOST'):
                        log.warning("Exit detected by 3-min poll, not WebSocket",
                                    tracker="scalper", action=scalp_update['action'],
                                    reason=scalp_update['reason'])

                # Evaluate new scalp opportunity via Claude agent
                if scalper.should_create(analysis):
                    signal = scalper.get_agent_signal(analysis, strikes_data)
                    if signal:
                        scalp_id = scalper.create_trade(signal, analysis, strikes_data)
                        if scalp_id:
                            self._register_trade_with_monitor(scalper)
            except Exception as e:
                log.error("Error in scalper agent", error=str(e))

            # ===== RALLY RIDER (Regime-adaptive, Claude-agent-powered) =====
            rr = self.strategies["rally_rider"]
            try:
                rr_update = rr.check_and_update(
                        strikes_data, analysis=analysis,
                        premium_monitor=self.premium_monitor)
                if rr_update:
                    log.info("RR trade updated",
                             action=rr_update['action'],
                             pnl=f"{rr_update['pnl']:.2f}%",
                             reason=rr_update['reason'])

                if rr.should_create(analysis):
                    rr_signal = rr.evaluate_signal(analysis, strikes_data)
                    if rr_signal:
                        rr_id = rr.create_trade(rr_signal, analysis, strikes_data)
                        if rr_id:
                            self._register_trade_with_monitor(rr)
            except Exception as e:
                log.error("Error in Rally Rider strategy", error=str(e))

            # Add scalper data to analysis for dashboard
            try:
                active_scalp = scalper.get_active()
                if active_scalp:
                    strike_sc = strikes_data.get(active_scalp["strike"], {})
                    key = "pe_ltp" if active_scalp["option_type"] == "PE" else "ce_ltp"
                    cur_prem = strike_sc.get(key, 0)
                    if cur_prem > 0:
                        sc_pnl = ((cur_prem - active_scalp["entry_premium"]) / active_scalp["entry_premium"]) * 100
                        active_scalp["current_premium"] = cur_prem
                        active_scalp["current_pnl"] = sc_pnl
                analysis["active_scalp_trade"] = active_scalp
                analysis["scalp_stats"] = scalper.get_stats()
            except Exception as e:
                log.error("Error getting scalper data for dashboard", error=str(e))
                analysis["active_scalp_trade"] = None
                analysis["scalp_stats"] = {}

            # Add RR data to analysis for dashboard
            try:
                active_rr = rr.get_active()
                if active_rr:
                    strike_rr = strikes_data.get(active_rr["strike"], {})
                    key_rr = "pe_ltp" if active_rr["option_type"] == "PE" else "ce_ltp"
                    cur_rr = strike_rr.get(key_rr, 0)
                    if cur_rr > 0:
                        rr_pnl = ((cur_rr - active_rr["entry_premium"]) / active_rr["entry_premium"]) * 100
                        active_rr["current_premium"] = cur_rr
                        active_rr["current_pnl"] = rr_pnl
                analysis["active_rr_trade"] = active_rr
                analysis["rr_stats"] = rr.get_stats()
            except Exception as e:
                log.error("Error getting RR data for dashboard", error=str(e))
                analysis["active_rr_trade"] = None
                analysis["rr_stats"] = {}

            # Run daily learning update at market close
            self._check_daily_learning_update()

            # Add chart history for frontend sync (last 30 data points)
            chart_history = get_analysis_history(limit=30)
            analysis["chart_history"] = chart_history

            # Add V-shape recovery status for frontend
            try:
                from analysis.v_shape import get_v_shape_status
                analysis["v_shape_status"] = get_v_shape_status() or {"signal_level": "NONE"}
            except Exception:
                analysis["v_shape_status"] = {"signal_level": "NONE"}

            # Broadcast to connected clients
            if self.socketio:
                self.socketio.emit("oi_update", analysis)
                log.debug("Emitted update to clients")

        except Exception as e:
            log.error("Error in fetch_and_analyze", error=str(e))
            import traceback
            traceback.print_exc()

    def start(self, interval_minutes: int = 3):
        """
        Start the scheduler.

        Args:
            interval_minutes: How often to fetch data (default 3 mins)
        """
        if self.is_running:
            log.warning("Scheduler already running")
            return

        # Calculate seconds until next candle-aligned minute
        # Candles align to :00, :03, :06, ..., :57 (since market opens 9:15, 15%3==0)
        from datetime import datetime as dt
        now = dt.now()
        current_min = now.minute
        next_aligned = current_min + (3 - current_min % 3) if current_min % 3 != 0 else current_min + 3
        seconds_to_next = (next_aligned - current_min) * 60 - now.second + 5  # +5s candle close offset
        if seconds_to_next <= 0:
            seconds_to_next += 180  # next 3-min window

        from datetime import timedelta
        start_date = now + timedelta(seconds=seconds_to_next)

        # Add the job — 3-min interval aligned to candles
        self.scheduler.add_job(
            self.fetch_and_analyze,
            trigger=IntervalTrigger(minutes=interval_minutes, start_date=start_date),
            id="oi_fetcher",
            name="Fetch OI Data (candle-aligned)",
            replace_existing=True
        )

        # 5-second live P&L broadcast (uses cached WebSocket LTP, no API calls)
        self.scheduler.add_job(
            self._broadcast_live_pnl,
            trigger=IntervalTrigger(seconds=5),
            id="pnl_broadcaster",
            name="Broadcast Live P&L (5s)",
            replace_existing=True
        )

        # 10-second orderflow depth collection (WebSocket MODE_FULL cache, no API calls)
        self.scheduler.add_job(
            self._save_orderflow_depth,
            trigger=IntervalTrigger(seconds=10),
            id="orderflow_collector",
            name="Collect Orderflow Depth (10s)",
            replace_existing=True
        )

        # Review reminder for display-only features (March 18, 2026)
        from apscheduler.triggers.date import DateTrigger
        review_date = datetime(2026, 3, 18, 10, 0, 0)
        self.scheduler.add_job(
            self._send_review_reminder,
            trigger=DateTrigger(run_date=review_date),
            id="review_reminder",
            name="Review Display Features Reminder",
            replace_existing=True
        )

        # Startup check: if past review date and reminder not sent, send now
        if datetime.now() >= review_date:
            self._send_review_reminder()

        # Start scheduler
        self.scheduler.start()
        self.is_running = True
        log.info("Scheduler started", interval=f"{interval_minutes}min",
                 next_run=start_date.strftime('%H:%M:%S'))

        # Start premium monitor and pick up existing active trades
        try:
            # Ensure instruments are loaded before scanning trades
            self.kite_fetcher._refresh_token()
            self.kite_fetcher._instrument_map.refresh()
            self.premium_monitor._instrument_map = self.kite_fetcher._instrument_map
            self.order_executor.set_instrument_map(self.kite_fetcher._instrument_map)
            self.order_executor._migrate_schema()
            self.premium_monitor.scan_existing_trades(self.strategies)
            self.premium_monitor.start()
        except Exception as e:
            log.error("Failed to start premium monitor", error=str(e))

        # First fetch at next aligned candle (no blocking startup fetch)

    def stop(self):
        """Stop the scheduler."""
        if self.is_running:
            self.scheduler.shutdown()
            self.is_running = False
            try:
                self.premium_monitor.stop()
            except Exception:
                pass
            log.info("Scheduler stopped")

    def get_last_analysis(self):
        """Get the most recent analysis result."""
        return self.last_analysis

    def trigger_now(self):
        """Manually trigger a fetch. Still respects market hours guard."""
        self.fetch_and_analyze()

    def _handle_premium_exit(self, exit_info: dict):
        """
        Callback from PremiumMonitor when SL/target hit is detected via WebSocket.

        Args:
            exit_info: Dict with trade_id, tracker_type, action, exit_premium, reason
        """
        trade_id = exit_info["trade_id"]
        tracker_type = exit_info["tracker_type"]
        action = exit_info["action"]
        exit_premium = exit_info.get("exit_premium", 0)
        reason = exit_info.get("reason", "")
        pnl = exit_info.get("pnl_pct", 0)

        log.info("Premium monitor exit detected",
                 trade_id=trade_id, tracker=tracker_type,
                 action=action, exit_premium=f"{exit_premium:.2f}",
                 reason=reason)

        try:
            strategy = self.strategies.get(tracker_type)
            if strategy:
                emoji = "\u2705" if action == "WON" else "\u274c"
                alert_msg = (
                    f"<b>{emoji} {tracker_type.upper()} {action}</b>\n"
                    f"{reason}\n"
                    f"Exit: \u20b9{exit_premium:.2f}\n"
                    f"P&L: {pnl:+.1f}%"
                )
                strategy.force_exit(trade_id, exit_premium, reason, pnl,
                                    alert_message=alert_msg)
            else:
                log.error("Unknown tracker type in premium exit", tracker=tracker_type)

            # Unregister from monitor
            self.premium_monitor.unregister_trade(trade_id)

        except Exception as e:
            log.error("Error handling premium exit", error=str(e),
                      trade_id=trade_id, tracker=tracker_type)

    def _check_daily_learning_update(self):
        """Daily learning update - self-learner removed, now a no-op."""
        pass

    def _send_review_reminder(self):
        """Send Telegram reminder to review display-only features."""
        from alerts import send_telegram
        message = (
            "<b>📊 OI Analyzer Review Reminder</b>\n\n"
            "2 weeks since display-only features were added.\n"
            "Time to review their usefulness:\n\n"
            "• <b>Primary S/R</b> — Are absolute OI support/resistance levels accurate?\n"
            "• <b>PCR Trend</b> — Does rising/falling PCR correlate with moves?\n"
            "• <b>Max Pain Drift</b> — Is drift direction useful for EOD prediction?\n"
            "• <b>2-Candle Confirmation</b> — Does confirmed verdict improve win rate?\n"
            "• <b>OI Flow Classification</b> — Does writing vs buying distinction help?\n\n"
            "Check <code>/api/latest</code> for all new fields."
        )
        try:
            send_telegram(message)
            log.info("Review reminder sent")
        except Exception as e:
            log.error("Failed to send review reminder", error=str(e))

    def _broadcast_live_pnl(self):
        """Emit lightweight P&L update from WebSocket LTP cache every 5s."""
        if not self.socketio or not self.is_market_open():
            return
        pnl_data = self.premium_monitor.get_live_pnl()
        if pnl_data:
            self.socketio.emit("pnl_update", pnl_data)

    def _save_orderflow_depth(self):
        """Save orderflow depth snapshot every 10s for active trades + core strikes."""
        if not self.is_market_open():
            return
        depth_records = self.premium_monitor.get_depth_snapshot()
        core_records = self.premium_monitor.get_core_depth_snapshot()
        # Merge, deduplicating by instrument_token
        seen_tokens = {r["instrument_token"] for r in depth_records}
        for r in core_records:
            if r["instrument_token"] not in seen_tokens:
                depth_records.append(r)
                seen_tokens.add(r["instrument_token"])
        if depth_records:
            save_orderflow_depth(depth_records)

    def get_market_status(self) -> dict:
        """Get current market status information."""
        now = datetime.now()
        is_open = self.is_market_open()

        return {
            "is_open": is_open,
            "current_time": now.strftime("%H:%M:%S"),
            "market_open": MARKET_OPEN.strftime("%H:%M"),
            "market_close": MARKET_CLOSE.strftime("%H:%M"),
            "day": now.strftime("%A"),
            "message": "Market is OPEN" if is_open else "Market is CLOSED",
            "self_learning": {
                "should_trade": True,
                "ema_accuracy": 0,
                "is_paused": False
            }
        }


if __name__ == "__main__":
    # Test scheduler standalone
    log.info("Starting OI Scheduler test")
    scheduler = OIScheduler()

    # Just run once for testing
    scheduler.fetch_and_analyze()

    log.info("Test complete")
