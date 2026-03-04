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

from kite_data import KiteDataFetcher
from premium_monitor import PremiumMonitor, ActiveTrade
from oi_analyzer import analyze_tug_of_war, calculate_market_trend
from database import (
    save_snapshot, save_analysis, purge_old_data, get_last_data_date,
    get_recent_price_trend, get_recent_oi_changes, get_previous_strikes_data,
    get_previous_futures_oi, get_analysis_history, get_previous_verdict,
    save_orderflow_depth, purge_old_orderflow,
    get_previous_smoothed_score
)
from trade_tracker import get_trade_tracker
from selling_tracker import SellingTracker, get_active_sell_setup
from dessert_tracker import DessertTracker, get_active_dessert
from momentum_tracker import MomentumTracker, get_active_momentum
from pa_tracker import PulseRiderTracker, get_active_pa
from database import get_active_trade_setup
from pattern_tracker import check_patterns, log_failed_entry
from prediction_engine import PredictionEngine
from logger import get_logger

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
        self.trade_tracker = get_trade_tracker()
        self.selling_tracker = SellingTracker()
        self.dessert_tracker = DessertTracker()
        self.momentum_tracker = MomentumTracker()
        self.pa_tracker = PulseRiderTracker()
        self.prediction_engine = PredictionEngine()
        self.force_enabled = False
        self.last_iron_pulse_time = None  # Track when Iron Pulse enters for selling decoupling
        # Kite data fetcher (reusable, no browser)
        self.kite_fetcher = KiteDataFetcher()
        # Premium monitor for real-time SL/target detection
        self.premium_monitor = PremiumMonitor(socketio=socketio, shadow_mode=False)
        self.premium_monitor.set_exit_callback(self._handle_premium_exit)

    def set_force_enabled(self, enabled: bool):
        """Enable/disable force fetch mode for automatic polling."""
        self.force_enabled = enabled

    def _register_trade_with_monitor(self, tracker_type: str, get_active_fn, is_selling: bool = False):
        """Register a newly created/activated trade with the WebSocket premium monitor."""
        try:
            setup = get_active_fn()
            if not setup:
                log.warning("WS registration skipped: no active trade found", tracker=tracker_type)
                return
            if not self.premium_monitor._instrument_map:
                log.warning("WS registration skipped: no instrument map", tracker=tracker_type)
                return
            expiry = self.premium_monitor._instrument_map.get_current_expiry()
            if not expiry:
                log.warning("WS registration skipped: no current expiry", tracker=tracker_type)
                return
            trade_obj = self.premium_monitor._db_trade_to_active(setup, tracker_type, expiry, is_selling=is_selling)
            if not trade_obj:
                log.warning("WS registration skipped: instrument token not found",
                            tracker=tracker_type, strike=setup.get('strike'), type=setup.get('option_type'))
                return
            self.premium_monitor.register_trade(trade_obj)
            log.info("Trade registered with WebSocket monitor",
                     tracker=tracker_type, trade_id=setup['id'],
                     strike=setup.get('strike'), type=setup.get('option_type'))
        except Exception as e:
            log.error("WS registration failed", tracker=tracker_type, error=str(e))

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
            from database import get_recent_pcr_values, get_recent_max_pain_values
            from oi_analyzer import calculate_pcr_trend, calculate_max_pain_drift
            pcr_history = get_recent_pcr_values(limit=10)
            analysis["pcr_trend"] = calculate_pcr_trend(pcr_history)

            # Display-only: Max pain drift
            mp_history = get_recent_max_pain_values(limit=20)
            analysis["max_pain_drift"] = calculate_max_pain_drift(
                mp_history, analysis.get("max_pain", 0), spot_price
            )

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

            # Prediction Tree: generate/match/deepen predictions
            try:
                prediction_result = self.prediction_engine.process_candle(analysis)
                if prediction_result:
                    analysis["prediction_tree"] = prediction_result
            except Exception as e:
                log.error("Error in prediction engine", error=str(e))

            # Pattern Tracker: detect patterns (PM reversal alerts disabled)
            try:
                check_patterns(analysis)
                # PM reversal alerts disabled - using new strategy instead
                analysis["call_alert"] = None
            except Exception as e:
                log.error("Error in pattern tracking", error=str(e))
                analysis["call_alert"] = None

            # Trade Tracker: manage persistent trade setups
            # 1. Check and update existing setup status (activation/resolution)
            trade_update = self.trade_tracker.check_and_update_setup(strikes_data, timestamp)
            if trade_update:
                log.info("Trade setup updated", setup_id=trade_update['setup_id'],
                         prev_status=trade_update['previous_status'], new_status=trade_update['new_status'])
                # Register with WebSocket when trade becomes ACTIVE
                if trade_update.get('new_status') == 'ACTIVE':
                    self._register_trade_with_monitor("iron_pulse", get_active_trade_setup)
                elif trade_update.get('new_status') in ('WON', 'LOST'):
                    log.warning("Exit detected by 3-min poll, not WebSocket",
                                tracker="iron_pulse", action=trade_update['new_status'])

            # 2. Cancel PENDING setup if OI direction flipped
            self.trade_tracker.cancel_on_direction_change(analysis["verdict"], timestamp)

            # 3. Expire PENDING setups at market close
            self.trade_tracker.expire_pending_setups(timestamp)

            # 4. Force close ACTIVE trades at market close (3:20 PM)
            self.trade_tracker.force_close_active_trades(timestamp, strikes_data)

            # 5. Create new setup if conditions met (pass price_history for timing checks)
            if self.trade_tracker.should_create_new_setup(analysis, price_history):
                self.trade_tracker.create_setup(analysis, timestamp)
                self.last_iron_pulse_time = timestamp  # Track for selling decoupling

            # ===== SELLING TRACKER =====
            try:
                # Check/update active selling trade
                sell_update = self.selling_tracker.check_and_update_sell_setup(strikes_data)
                if sell_update:
                    log.info("Sell trade updated", action=sell_update['action'],
                             pnl=f"{sell_update['pnl']:.2f}%", reason=sell_update['reason'])
                    if sell_update['action'] in ('WON', 'LOST'):
                        log.warning("Exit detected by 3-min poll, not WebSocket",
                                    tracker="selling", action=sell_update['action'],
                                    reason=sell_update['reason'])

                # Selling decoupling: delay 6 minutes after Iron Pulse entry
                # Prevents both strategies from losing on the same signal
                selling_deferred = False
                if self.last_iron_pulse_time:
                    seconds_since_ip = (timestamp - self.last_iron_pulse_time).total_seconds()
                    if seconds_since_ip < 360:  # 6 minutes
                        selling_deferred = True
                        log.info("Selling deferred: Iron Pulse entered recently",
                                 seconds_since=f"{seconds_since_ip:.0f}s",
                                 required="360s",
                                 reason="decoupling")

                # Create new selling setup if conditions met (and not deferred)
                if not selling_deferred and self.selling_tracker.should_create_sell_setup(analysis):
                    sell_id = self.selling_tracker.create_sell_setup(analysis, strikes_data)
                    if sell_id:
                        self._register_trade_with_monitor("selling", get_active_sell_setup, is_selling=True)
            except Exception as e:
                log.error("Error in selling tracker", error=str(e))

            # ===== DESSERT TRACKER (1:2 RR strategies) =====
            try:
                # Check/update active dessert trade
                dessert_update = self.dessert_tracker.check_and_update_dessert(strikes_data)
                if dessert_update:
                    log.info("Dessert trade updated",
                             strategy=dessert_update.get('strategy'),
                             action=dessert_update['action'],
                             pnl=f"{dessert_update['pnl']:.2f}%",
                             reason=dessert_update['reason'])
                    if dessert_update['action'] in ('WON', 'LOST'):
                        log.warning("Exit detected by 3-min poll, not WebSocket",
                                    tracker="dessert", action=dessert_update['action'],
                                    reason=dessert_update['reason'])

                # Create new dessert trade if conditions met
                strategy = self.dessert_tracker.should_create_dessert(analysis)
                if strategy:
                    dessert_id = self.dessert_tracker.create_dessert_trade(strategy, analysis, strikes_data)
                    if dessert_id:
                        self._register_trade_with_monitor("dessert", get_active_dessert)
            except Exception as e:
                log.error("Error in dessert tracker", error=str(e))

            # ===== MOMENTUM TRACKER (1:2 RR trend-following) =====
            try:
                # Check/update active momentum trade
                momentum_update = self.momentum_tracker.check_and_update_momentum(strikes_data)
                if momentum_update:
                    log.info("Momentum trade updated",
                             action=momentum_update['action'],
                             pnl=f"{momentum_update['pnl']:.2f}%",
                             reason=momentum_update['reason'])
                    if momentum_update['action'] in ('WON', 'LOST'):
                        log.warning("Exit detected by 3-min poll, not WebSocket",
                                    tracker="momentum", action=momentum_update['action'],
                                    reason=momentum_update['reason'])

                # Create new momentum trade if conditions met
                direction = self.momentum_tracker.should_create_momentum(analysis)
                if direction:
                    mom_id = self.momentum_tracker.create_momentum_trade(direction, analysis, strikes_data)
                    if mom_id:
                        self._register_trade_with_monitor("momentum", get_active_momentum)
            except Exception as e:
                log.error("Error in momentum tracker", error=str(e))

            # ===== PRICE ACTION TRACKER (CHC-3 premium momentum) =====
            try:
                # Check/update active PA trade
                pa_update = self.pa_tracker.check_and_update_trade(strikes_data, timestamp)
                if pa_update:
                    log.info("PA trade updated",
                             action=pa_update['action'],
                             pnl=f"{pa_update['pnl']:.2f}%",
                             reason=pa_update['reason'])
                    if pa_update['action'] in ('WON', 'LOST'):
                        log.warning("Exit detected by 3-min poll, not WebSocket",
                                    tracker="pa", action=pa_update['action'],
                                    reason=pa_update['reason'])

                # Create new PA trade if conditions met
                pa_side = self.pa_tracker.should_create_trade(analysis, strikes_data)
                if pa_side:
                    pa_trade_id = self.pa_tracker.create_trade(pa_side, analysis, strikes_data)
                    if pa_trade_id:
                        self._register_trade_with_monitor("pa", get_active_pa)
            except Exception as e:
                log.error("Error in PA tracker", error=str(e))

            # 6. Add trade tracker data to analysis for dashboard
            tracker_data = self.trade_tracker.get_stats()
            active_setup_with_pnl = self.trade_tracker.get_active_setup_with_pnl(strikes_data)
            analysis["active_trade"] = active_setup_with_pnl
            analysis["trade_stats"] = tracker_data["stats"]

            # Add selling tracker data
            try:
                active_sell = get_active_sell_setup()
                if active_sell:
                    # Calculate current P&L for active sell
                    strike_d = strikes_data.get(active_sell["strike"], {})
                    opt = active_sell["option_type"]
                    cur_prem = strike_d.get("ce_ltp" if opt == "CE" else "pe_ltp", 0)
                    if cur_prem > 0:
                        sell_pnl = ((active_sell["entry_premium"] - cur_prem) / active_sell["entry_premium"]) * 100
                        active_sell["current_premium"] = cur_prem
                        active_sell["current_pnl"] = sell_pnl
                analysis["active_sell_trade"] = active_sell
                analysis["sell_stats"] = self.selling_tracker.get_sell_stats()
            except Exception as e:
                log.error("Error getting sell data for dashboard", error=str(e))
                analysis["active_sell_trade"] = None
                analysis["sell_stats"] = {}

            # Add dessert tracker data
            try:
                active_dessert = get_active_dessert()
                if active_dessert:
                    strike_d = strikes_data.get(active_dessert["strike"], {})
                    cur_prem = strike_d.get("pe_ltp", 0)
                    if cur_prem > 0:
                        d_pnl = ((cur_prem - active_dessert["entry_premium"]) / active_dessert["entry_premium"]) * 100
                        active_dessert["current_premium"] = cur_prem
                        active_dessert["current_pnl"] = d_pnl
                analysis["active_dessert_trade"] = active_dessert
                analysis["dessert_stats"] = self.dessert_tracker.get_dessert_stats()
            except Exception as e:
                log.error("Error getting dessert data for dashboard", error=str(e))
                analysis["active_dessert_trade"] = None
                analysis["dessert_stats"] = {}

            # Add momentum tracker data
            try:
                active_momentum = get_active_momentum()
                if active_momentum:
                    strike_m = strikes_data.get(active_momentum["strike"], {})
                    key = "pe_ltp" if active_momentum["option_type"] == "PE" else "ce_ltp"
                    cur_prem = strike_m.get(key, 0)
                    if cur_prem > 0:
                        m_pnl = ((cur_prem - active_momentum["entry_premium"]) / active_momentum["entry_premium"]) * 100
                        active_momentum["current_premium"] = cur_prem
                        active_momentum["current_pnl"] = m_pnl
                analysis["active_momentum_trade"] = active_momentum
                analysis["momentum_stats"] = self.momentum_tracker.get_momentum_stats()
            except Exception as e:
                log.error("Error getting momentum data for dashboard", error=str(e))
                analysis["active_momentum_trade"] = None
                analysis["momentum_stats"] = {}

            # Add PA tracker data
            try:
                active_pa = get_active_pa()
                if active_pa:
                    strike_pa = strikes_data.get(active_pa["strike"], {})
                    key = "pe_ltp" if active_pa["option_type"] == "PE" else "ce_ltp"
                    cur_prem = strike_pa.get(key, 0)
                    if cur_prem > 0:
                        pa_pnl = ((cur_prem - active_pa["entry_premium"]) / active_pa["entry_premium"]) * 100
                        active_pa["current_premium"] = cur_prem
                        active_pa["current_pnl"] = pa_pnl
                analysis["active_pa_trade"] = active_pa
                analysis["pa_stats"] = self.pa_tracker.get_pa_stats()
            except Exception as e:
                log.error("Error getting PA data for dashboard", error=str(e))
                analysis["active_pa_trade"] = None
                analysis["pa_stats"] = {}

            # Run daily learning update at market close
            self._check_daily_learning_update()

            # Add chart history for frontend sync (last 30 data points)
            chart_history = get_analysis_history(limit=30)
            analysis["chart_history"] = chart_history

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
            self.premium_monitor.scan_existing_trades()
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

        log.info("Premium monitor exit detected",
                 trade_id=trade_id, tracker=tracker_type,
                 action=action, exit_premium=f"{exit_premium:.2f}",
                 reason=reason)

        try:
            from datetime import datetime as dt
            now = dt.now()

            if tracker_type == "iron_pulse":
                from database import update_trade_setup_status
                status = "WON" if action == "WON" else "LOST"
                update_trade_setup_status(
                    trade_id, status,
                    resolved_at=now.isoformat(),
                    exit_premium=exit_premium,
                    hit_sl=(action == "LOST"),
                    hit_target=(action == "WON"),
                )
            elif tracker_type == "selling":
                from database import get_connection
                with get_connection() as conn:
                    status = "WON" if action == "WON" else "LOST"
                    pnl = exit_info.get("pnl_pct", 0)
                    conn.execute("""
                        UPDATE sell_trade_setups SET status=?, resolved_at=?,
                        exit_premium=?, exit_reason=?, profit_loss_pct=?
                        WHERE id=?
                    """, (status, now.isoformat(), exit_premium, reason, pnl, trade_id))
                    conn.commit()
            elif tracker_type == "dessert":
                from database import get_connection
                with get_connection() as conn:
                    status = "WON" if action == "WON" else "LOST"
                    pnl = exit_info.get("pnl_pct", 0)
                    conn.execute("""
                        UPDATE dessert_trades SET status=?, resolved_at=?,
                        exit_premium=?, exit_reason=?, profit_loss_pct=?
                        WHERE id=?
                    """, (status, now.isoformat(), exit_premium, reason, pnl, trade_id))
                    conn.commit()
            elif tracker_type == "momentum":
                from database import get_connection
                with get_connection() as conn:
                    status = "WON" if action == "WON" else "LOST"
                    pnl = exit_info.get("pnl_pct", 0)
                    conn.execute("""
                        UPDATE momentum_trades SET status=?, resolved_at=?,
                        exit_premium=?, exit_reason=?, profit_loss_pct=?
                        WHERE id=?
                    """, (status, now.isoformat(), exit_premium, reason, pnl, trade_id))
                    conn.commit()
            elif tracker_type == "pa":
                from database import get_connection
                with get_connection() as conn:
                    status = "WON" if action == "WON" else "LOST"
                    pnl = exit_info.get("pnl_pct", 0)
                    conn.execute("""
                        UPDATE pa_trades SET status=?, resolved_at=?,
                        exit_premium=?, exit_reason=?, profit_loss_pct=?
                        WHERE id=?
                    """, (status, now.isoformat(), exit_premium, reason, pnl, trade_id))
                    conn.commit()

            # Unregister from monitor
            self.premium_monitor.unregister_trade(trade_id)

            # Send Telegram alert via the appropriate tracker method
            try:
                if tracker_type == "selling":
                    from selling_tracker import get_connection
                    with get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT * FROM sell_trade_setups WHERE id = ?", (trade_id,))
                        row = cursor.fetchone()
                        if row:
                            setup = dict(row)
                            pnl = exit_info.get("pnl_pct", 0)
                            exit_reason = "TARGET2" if action == "WON" else "SL"
                            self.selling_tracker._send_exit_alert(setup, exit_premium, exit_reason, pnl)
                else:
                    from alerts import send_telegram
                    emoji = "\U0001f7e2" if action == "WON" else "\U0001f534"
                    msg = f"{emoji} {tracker_type.upper()} {action}\n{reason}\nExit: \u20b9{exit_premium:.2f}"
                    send_telegram(msg)
            except Exception as e:
                log.error("Failed to send exit alert", error=str(e),
                          trade_id=trade_id, tracker=tracker_type)

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
        """Save orderflow depth snapshot every 10s for active trades."""
        if not self.is_market_open():
            return
        depth_records = self.premium_monitor.get_depth_snapshot()
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
