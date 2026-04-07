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

from kite.data import KiteDataFetcher, NIFTY_TOKEN
from monitoring.tick_hub import TickHub
from monitoring.candle_builder import CandleBuilder
from monitoring.exit_monitor import ExitMonitor, ActiveTrade
from monitoring.orderflow_collector import OrderflowCollector
from monitoring.live_pnl_broadcaster import LivePnlBroadcaster
from analysis.tug_of_war import analyze_tug_of_war, calculate_market_trend
from db.legacy import (
    save_snapshot, save_analysis, purge_old_data, get_last_data_date,
    get_recent_price_trend, get_recent_oi_changes, get_previous_strikes_data,
    get_previous_futures_oi, get_analysis_history, get_previous_verdict,
    save_orderflow_depth, purge_old_orderflow, purge_old_live_candles,
    get_previous_smoothed_score
)
from db.trade_repo import TradeRepository
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
        # Kite data fetcher — used by CandleBuilder for history bootstrap/gap-fill
        self.kite_fetcher = KiteDataFetcher()

        # ----- TickHub architecture -----
        # One KiteTicker shared by all consumers.
        self.tick_hub = TickHub()
        # CandleBuilder builds 1-min + 3-min OHLC from live ticks + bootstrap.
        self.candle_builder = CandleBuilder(
            kite_fetcher=self.kite_fetcher, tick_hub=self.tick_hub,
        )
        # ExitMonitor replaces premium_monitor's SL/target/soft-SL logic.
        self.exit_monitor = ExitMonitor(tick_hub=self.tick_hub, shadow_mode=False)
        self.exit_monitor.set_exit_callback(self._handle_premium_exit)
        # OrderflowCollector caches depth; scheduler 10s job drains it.
        self.orderflow_collector = OrderflowCollector(tick_hub=self.tick_hub)
        # LivePnlBroadcaster caches LTP; scheduler 5s job emits payload.
        self.live_pnl_broadcaster = LivePnlBroadcaster(exit_monitor=self.exit_monitor)

        # Register all consumers with the hub (order does not matter).
        self.tick_hub.add_consumer(self.candle_builder)
        self.tick_hub.add_consumer(self.exit_monitor)
        self.tick_hub.add_consumer(self.orderflow_collector)
        self.tick_hub.add_consumer(self.live_pnl_broadcaster)

        repo = TradeRepository()
        self.strategies = {
            "rally_rider": RRStrategy(
                trade_repo=repo,
                order_executor=self.order_executor,
                exit_monitor=self.exit_monitor,
                candle_builder=self.candle_builder,
            ),
        }
        self._alert_broker = AlertBroker()
        self.v_shape_detector = VShapeDetector()
        self.force_enabled = False

    def set_force_enabled(self, enabled: bool):
        """Enable/disable force fetch mode for automatic polling."""
        self.force_enabled = enabled

    def _register_trade_with_monitor(self, strategy):
        """Register a newly created/activated trade with the ExitMonitor."""
        try:
            setup = strategy.get_active()
            if not setup:
                log.warning("WS registration skipped: no active trade found", tracker=strategy.tracker_type)
                return
            if not self.exit_monitor._instrument_map:
                log.warning("WS registration skipped: no instrument map", tracker=strategy.tracker_type)
                return
            expiry = self.exit_monitor._instrument_map.get_current_expiry()
            if not expiry:
                log.warning("WS registration skipped: no current expiry", tracker=strategy.tracker_type)
                return
            trade_obj = self.exit_monitor._db_trade_to_active(
                setup, strategy.tracker_type, expiry, is_selling=strategy.is_selling)
            if not trade_obj:
                log.warning("WS registration skipped: instrument token not found",
                            tracker=strategy.tracker_type, strike=setup.get('strike'), type=setup.get('option_type'))
                return
            self.exit_monitor.register_trade(trade_obj)
            log.info("Trade registered with ExitMonitor",
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
        """Purge old data + update nifty/vix history for regime classification."""
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

        # Purge live candles (30-day rolling window)
        purge_old_live_candles(days=30)

        # Update nifty_history + vix_history for regime classification
        self._update_history_tables()

        self.last_purge_date = today

    def _update_history_tables(self):
        """Fetch recent NIFTY + VIX 3-min candles from Kite historical API.

        Fills the gap between the last stored timestamp and yesterday.
        Called once per day at startup to keep regime classification current.
        """
        try:
            from datetime import timedelta
            from db.connection import DB_PATH
            import sqlite3

            NIFTY_TOKEN = 256265
            VIX_TOKEN = 264969

            self.kite_fetcher._refresh_token()
            kite = self.kite_fetcher._kite

            # Use direct sqlite3 connection with explicit commit
            conn = sqlite3.connect(DB_PATH)

            for table, token, label in [
                ("nifty_history", NIFTY_TOKEN, "NIFTY"),
                ("vix_history", VIX_TOKEN, "VIX"),
            ]:
                try:
                    row = conn.execute(
                        f"SELECT MAX(timestamp) FROM {table}"
                    ).fetchone()
                    last_ts = row[0] if row and row[0] else None

                    if last_ts:
                        last_date = datetime.strptime(last_ts[:10], "%Y-%m-%d").date()
                    else:
                        last_date = (datetime.now() - timedelta(days=10)).date()

                    from_date = datetime.combine(
                        last_date + timedelta(days=1), datetime.min.time())
                    to_date = datetime.combine(
                        datetime.now().date() - timedelta(days=1),
                        datetime.max.time().replace(microsecond=0))

                    if from_date.date() > to_date.date():
                        log.debug(f"{label} history up to date", last=str(last_date))
                        continue

                    log.info(f"Updating {label} history",
                             from_date=str(from_date.date()),
                             to_date=str(to_date.date()))

                    data = kite.historical_data(
                        token, from_date, to_date, "3minute")

                    for row in data:
                        ts = row["date"].strftime("%Y-%m-%d %H:%M:%S")
                        conn.execute(
                            f"INSERT OR IGNORE INTO {table} "
                            f"(timestamp, open, high, low, close, volume) "
                            f"VALUES (?, ?, ?, ?, ?, ?)",
                            (ts, row["open"], row["high"], row["low"],
                             row["close"], row.get("volume", 0)))

                    conn.commit()
                    log.info(f"{label} history updated",
                             fetched=len(data), inserted=len(data))

                except Exception as e:
                    log.error(f"Failed to update {label} history", error=str(e))

            conn.close()

        except Exception as e:
            log.error("Failed to update history tables", error=str(e))

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

            # Update core strike subscriptions for orderflow collection (6 strikes)
            active_trade_tokens = set(self.exit_monitor._token_to_trades.keys())
            self.orderflow_collector.update_core_strikes(
                spot_price, active_trade_tokens=active_trade_tokens,
            )

            # Rotate CandleBuilder option strike subscriptions
            # (ATM±50, ATM±100, ATM±150 = 6 CE + 6 PE)
            try:
                atm = round(spot_price / 50) * 50
                ce_strikes = [atm - 150, atm - 100, atm - 50]
                pe_strikes = [atm + 50, atm + 100, atm + 150]
                self.candle_builder.set_option_strikes(
                    ce_strikes=ce_strikes,
                    pe_strikes=pe_strikes,
                    expiry=current_expiry,
                    spot=spot_price,
                    instrument_map=self.kite_fetcher._instrument_map,
                )
            except Exception as e:
                log.error("CandleBuilder strike rotation failed", error=str(e))

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

            # Attach live candles from CandleBuilder to analysis dict so
            # strategies can consume them without fetching live.
            try:
                atm = round(spot_price / 50) * 50
                ce_label = f"NIFTY_{atm - 100}_CE"
                pe_label = f"NIFTY_{atm + 100}_PE"
                analysis["nifty_1min_candles"] = self.candle_builder.get_candles("NIFTY", "1min")
                analysis["nifty_3min_candles"] = self.candle_builder.get_candles("NIFTY", "3min")
                analysis["ce_candles"] = self.candle_builder.get_candles(ce_label, "3min")
                analysis["pe_candles"] = self.candle_builder.get_candles(pe_label, "3min")
                analysis["ce_strike"] = atm - 100
                analysis["pe_strike"] = atm + 100
            except Exception as e:
                log.error("Failed to attach candles to analysis dict", error=str(e))
                analysis.setdefault("nifty_1min_candles", [])
                analysis.setdefault("nifty_3min_candles", [])
                analysis.setdefault("ce_candles", [])
                analysis.setdefault("pe_candles", [])

            # ===== RALLY RIDER (Regime-adaptive, Claude-agent-powered) =====
            rr = self.strategies["rally_rider"]
            try:
                rr_update = rr.check_and_update(
                        strikes_data, analysis=analysis,
                        exit_monitor=self.exit_monitor)
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
            # Strip CandleBuilder candle arrays before emit — they contain
            # datetime objects (not JSON serializable) and the dashboard
            # doesn't consume them anyway. Strategies have already read them
            # by this point.
            if self.socketio:
                emit_payload = {
                    k: v for k, v in analysis.items()
                    if k not in (
                        "ce_candles", "pe_candles",
                        "nifty_1min_candles", "nifty_3min_candles",
                    )
                }
                self.socketio.emit("oi_update", emit_payload)
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

        # Start TickHub + consumers + pick up existing active trades
        try:
            # 1. Ensure instruments are loaded (needed by ExitMonitor,
            #    OrderflowCollector, and CandleBuilder bootstrap).
            self.kite_fetcher._refresh_token()
            self.kite_fetcher._instrument_map.refresh()
            self.exit_monitor._instrument_map = self.kite_fetcher._instrument_map
            self.orderflow_collector._instrument_map = self.kite_fetcher._instrument_map
            self.order_executor.set_instrument_map(self.kite_fetcher._instrument_map)
            self.order_executor._migrate_schema()

            # 2. Register NIFTY spot with CandleBuilder (other instruments
            #    register dynamically on first fetch_and_analyze once spot is known).
            self.candle_builder.register_instrument(
                label="NIFTY", token=NIFTY_TOKEN,
                instr_type="index", intervals=("1min", "3min"),
            )

            # 3. Bootstrap candle history (one-time historical_data fetch).
            try:
                self.candle_builder.bootstrap()
            except Exception as e:
                log.error("CandleBuilder bootstrap failed", error=str(e))

            # 4. Pick up any active trades from the DB.
            self.exit_monitor.scan_existing_trades(self.strategies)

            # 5. Start the WebSocket (after consumer state is warm).
            access_token = self.kite_fetcher._kite.access_token if hasattr(
                self.kite_fetcher._kite, "access_token") else None
            if access_token is None:
                from kite.auth import load_token
                access_token = load_token()
            import os
            self.tick_hub._api_key = os.environ.get("KITE_API_KEY", "")
            self.tick_hub._access_token = access_token or ""
            self.tick_hub.start()
        except Exception as e:
            log.error("Failed to start TickHub services", error=str(e))

        # First fetch at next aligned candle (no blocking startup fetch)

    def stop(self):
        """Stop the scheduler."""
        if self.is_running:
            self.scheduler.shutdown()
            self.is_running = False
            try:
                self.tick_hub.stop()
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
        """Callback from ExitMonitor when SL/target hit is detected via WebSocket.

        Args:
            exit_info: Dict with trade_id, tracker_type, action, exit_premium, reason
        """
        trade_id = exit_info["trade_id"]
        tracker_type = exit_info["tracker_type"]
        action = exit_info["action"]
        exit_premium = exit_info.get("exit_premium", 0)
        reason = exit_info.get("reason", "")
        pnl = exit_info.get("pnl_pct", 0)

        log.info("Exit monitor exit detected",
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
                log.error("Unknown tracker type in exit callback", tracker=tracker_type)

            # Unregister from ExitMonitor (releases TickHub token subscription)
            self.exit_monitor.unregister_trade(trade_id)

        except Exception as e:
            log.error("Error handling exit", error=str(e),
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
        pnl_data = self.live_pnl_broadcaster.get_pnl_payload()
        if pnl_data:
            self.socketio.emit("pnl_update", pnl_data)

    def _save_orderflow_depth(self):
        """Save orderflow depth snapshot every 10s for active trades + core strikes."""
        if not self.is_market_open():
            return
        active_trades = {
            t.instrument_token: t
            for t in self.exit_monitor._all_trades.values()
        }
        depth_records = self.orderflow_collector.collect_snapshots(
            active_trades_by_token=active_trades
        )
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
