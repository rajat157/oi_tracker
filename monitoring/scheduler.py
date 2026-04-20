"""
APScheduler for periodic OI data fetching
Runs every 3 minutes to match NSE update frequency
Only runs during market hours (9:15 AM - 3:30 PM IST, weekdays)
"""

import json
import os
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
from strategies.intraday_hunter import IntradayHunterStrategy
from config import IntradayHunterConfig
from kite.order_executor import OrderExecutor
from alerts.broker import AlertBroker
from analysis.v_shape import VShapeDetector
from analysis.pattern_tracker import check_patterns, log_failed_entry
from core.logger import get_logger

log = get_logger("scheduler")


# Market timing constants (IST)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

# Kite instrument tokens for IntradayHunter (BN/SX + the two BN constituents
# used by the R29 confluence filter).
BANKNIFTY_TOKEN = 260105
SENSEX_TOKEN = 265
HDFCBANK_TOKEN = 341249
KOTAKBANK_TOKEN = 492033

IH_INSTRUMENTS = (
    ("BANKNIFTY", BANKNIFTY_TOKEN, "index"),
    ("SENSEX", SENSEX_TOKEN, "index"),
    ("HDFCBANK", HDFCBANK_TOKEN, "stock"),
    ("KOTAKBANK", KOTAKBANK_TOKEN, "stock"),
)


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
        # IntradayHunter — registered only when env flag is on. Even when
        # registered, all trades default to PAPER unless IH_LIVE_INDICES
        # explicitly opts an index in.
        self._ih_cfg = IntradayHunterConfig()
        self._multi_imap = None  # MultiInstrumentMap, lazily initialized in start()
        if self._ih_cfg.ENABLED:
            # Pre-create the multi-imap so it's available before start().
            # Will be refreshed (CSV download) in start() once auth is ready.
            from kite.instruments import MultiInstrumentMap
            self._multi_imap = MultiInstrumentMap(
                api_key=os.environ.get("KITE_API_KEY", ""),
            )
            self.strategies["intraday_hunter"] = IntradayHunterStrategy(
                trade_repo=repo,
                order_executor=self.order_executor,
                exit_monitor=self.exit_monitor,
                candle_builder=self.candle_builder,
                kite_fetcher=self.kite_fetcher,
                multi_instrument_map=self._multi_imap,
                socketio=self.socketio,
            )
            log.info("IntradayHunter strategy enabled",
                     live_indices=sorted(self._ih_cfg.LIVE_INDICES) or "(all paper)",
                     lots=self._ih_cfg.LOTS)
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

    def _register_ih_trades_with_monitor(self, ih) -> None:
        """Register all active IH positions with ExitMonitor (post-fill).

        Unlike RR (single trade), IH has up to 3 positions per signal group.
        Called AFTER create_trade returns so DB has corrected fill prices.
        """
        try:
            active = ih._fetch_active_positions()
            if not active:
                return
            for pos in active:
                ih._register_with_exit_monitor(pos["id"], pos["index_label"], pos)
        except Exception as e:
            log.error("IH ExitMonitor batch registration failed", error=str(e))

    def _run_intraday_hunter_minute(self) -> None:
        """1-minute IntradayHunter cycle.

        Runs separately from the 3-min OI fetch loop. Pulls only what IH
        needs (no option chain refetch — that's expensive and unnecessary
        for IH which works on index 1-min candles). Calls the strategy's
        check_and_update + should_create/evaluate_signal/create_trade.
        """
        if not self.is_market_open():
            log.debug("IH cycle: market closed, skipping")
            return
        ih = self.strategies.get("intraday_hunter")
        if ih is None:
            log.warning("IH cycle: strategy not registered, skipping")
            return
        log.debug("IH cycle: running", ts=datetime.now().strftime("%H:%M:%S"))

        try:
            # Build a minimal analysis dict — IH only reads candles + spot + vix
            analysis: dict = {}
            spot = 0.0
            try:
                ny_full = self.candle_builder.get_candles("NIFTY", "1min") or []
                ny = self._today_only(ny_full)
                analysis["nifty_1min_candles"] = ny
                if ny_full:
                    spot = float(ny_full[-1]["close"])
            except Exception as e:
                log.error("IH cycle: failed to read NIFTY candles", error=str(e))
                analysis["nifty_1min_candles"] = []

            analysis["spot_price"] = spot

            # Best-effort VIX from the last full analysis (refreshed every 3 min)
            if isinstance(self.last_analysis, dict):
                analysis["vix"] = self.last_analysis.get("vix", 0)
            else:
                analysis["vix"] = 0

            # IH-specific inputs (BN/SX/HDFC/KOTAK candles, spots, yesterday)
            self._attach_ih_inputs(analysis)

            # Position monitoring first — exit before entering anything new
            ih_update = ih.check_and_update({}, analysis=analysis)
            if ih_update and ih_update.get("closed"):
                for closed in ih_update["closed"]:
                    log.info("IH position closed",
                             trade_id=closed.get("trade_id"),
                             label=closed.get("label"),
                             reason=closed.get("exit_reason"),
                             pnl=f"{closed.get('pnl_rs', 0):+.0f}")

            # Signal evaluation
            can_create = ih.should_create(analysis)
            log.info("IH cycle gate check",
                     can_create=can_create,
                     spot=analysis.get("spot_price", 0),
                     ny_candles=len(analysis.get("nifty_1min_candles") or []),
                     bn_candles=len(analysis.get("banknifty_1min_candles") or []),
                     sx_candles=len(analysis.get("sensex_1min_candles") or []),
                     yday_candles=len(analysis.get("nifty_yesterday_candles") or []),
                     vix=analysis.get("vix", 0))
            if can_create:
                ih_signal = ih.evaluate_signal(analysis)
                log.info("IH cycle evaluate_signal",
                         signal=str(ih_signal.get("signal").trigger) if ih_signal else "None")
                if ih_signal:
                    trade_id = ih.create_trade(ih_signal, analysis, {})
                    if trade_id:
                        self._register_ih_trades_with_monitor(ih)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log.error("Error in IntradayHunter 1-min cycle",
                      error=str(e), traceback=tb[-1500:])
            traceback.print_exc()

    @staticmethod
    def _today_only(candles: list) -> list:
        """Filter a CandleBuilder buffer to *today's market session only*.

        CandleBuilder buffers hold the last 240 minutes (4h), which spans
        yesterday's tail in the morning. The IH engine treats its candle
        list positionally — `today[0].open` MUST be today's first 1-min
        candle (i.e. the 09:15 bar), not yesterday's 11:35.

        Also strips pre-market candles (before 09:15) which carry stale
        prices from the previous close or pre-open auction and corrupt
        gap/signal detection.
        """
        today = datetime.now().date()
        market_open = time(9, 15)
        out = []
        for c in candles or []:
            ts = c.get("date") or c.get("timestamp")
            if hasattr(ts, "date") and ts.date() == today:
                if hasattr(ts, "time") and ts.time() >= market_open:
                    out.append(c)
        return out

    def _rotate_ih_index_strikes(self, index_label: str, spacing: int) -> None:
        """Pre-emptively subscribe to ATM±spacing option strikes for an IH index.

        Called from the 3-min OI cycle. The result is that BN and SX option
        premiums are continuously available in CandleBuilder via real
        WebSocket ticks — so when an IH trade opens, the entry premium AND
        the monitoring premium use real LTP instead of Black-Scholes.

        Pulls the latest spot from CandleBuilder (no extra REST call) and
        rotates 3 CE + 3 PE strikes around it.
        """
        try:
            cb = self.candle_builder
            candles = cb.get_candles(index_label, "1min") or []
            if not candles:
                log.debug(f"{index_label} option strike rotation skipped — no candles yet")
                return
            spot = float(candles[-1]["close"])
            if spot <= 0:
                return
            atm = int(round(spot / spacing) * spacing)

            child_imap = self._multi_imap.get(index_label) if self._multi_imap else None
            if child_imap is None:
                log.warning(f"{index_label} option strike rotation: no instrument_map")
                return
            expiry = child_imap.get_current_expiry()
            if not expiry:
                log.warning(f"{index_label} option strike rotation: no expiry")
                return

            ce_strikes = [atm - spacing, atm, atm + spacing]
            pe_strikes = [atm - spacing, atm, atm + spacing]
            cb.set_option_strikes(
                ce_strikes=ce_strikes,
                pe_strikes=pe_strikes,
                expiry=expiry,
                spot=spot,
                instrument_map=child_imap,
                index_label=index_label,
            )
        except Exception as e:
            log.error(f"{index_label} option strike rotation failed", error=str(e))

    def _attach_ih_inputs(self, analysis: dict) -> None:
        """Populate analysis with the candle/spot fields IntradayHunter needs.

        - BN/SX/HDFC/KOTAK 1-min candles from CandleBuilder (TODAY ONLY)
        - Latest BN/SX spot from the most recent candle close
        - Yesterday's NIFTY 1-min candles from instrument_history
        """
        cb = self.candle_builder
        bn_full = cb.get_candles("BANKNIFTY", "1min") or []
        sx_full = cb.get_candles("SENSEX", "1min") or []
        bn = self._today_only(bn_full)
        sx = self._today_only(sx_full)
        analysis["banknifty_1min_candles"] = bn
        analysis["sensex_1min_candles"] = sx
        analysis["hdfcbank_1min_candles"] = self._today_only(
            cb.get_candles("HDFCBANK", "1min") or [])
        analysis["kotakbank_1min_candles"] = self._today_only(
            cb.get_candles("KOTAKBANK", "1min") or [])
        # Spot from latest tick is fine even if buffer mixes days — last
        # closed candle is always the most recent.
        analysis["banknifty_spot"] = float(bn_full[-1]["close"]) if bn_full else 0.0
        analysis["sensex_spot"] = float(sx_full[-1]["close"]) if sx_full else 0.0
        analysis["nifty_yesterday_candles"] = self._load_yesterday_nifty_candles()

    def _load_yesterday_nifty_candles(self) -> list:
        """Load yesterday's NIFTY 1-min candles from instrument_history.

        Returns the most recent prior trading day's full session as a list
        of dicts with keys (date, open, high, low, close, volume).
        Returns [] if the table is empty or no prior data exists.
        """
        from db.connection import DB_PATH
        import sqlite3
        try:
            conn = sqlite3.connect(DB_PATH)
            try:
                row = conn.execute(
                    "SELECT MAX(DATE(timestamp)) FROM instrument_history "
                    "WHERE label = 'NIFTY' AND interval = '1min' "
                    "  AND DATE(timestamp) < DATE('now', 'localtime')"
                ).fetchone()
                if not row or not row[0]:
                    return []
                prev_date = row[0]
                rows = conn.execute(
                    "SELECT timestamp, open, high, low, close, volume "
                    "FROM instrument_history "
                    "WHERE label = 'NIFTY' AND interval = '1min' "
                    "  AND DATE(timestamp) = ? "
                    "ORDER BY timestamp ASC",
                    (prev_date,),
                ).fetchall()
                return [
                    {"date": ts, "open": o, "high": h, "low": lo, "close": c, "volume": v}
                    for ts, o, h, lo, c, v in rows
                ]
            finally:
                conn.close()
        except Exception as e:
            log.error("_load_yesterday_nifty_candles failed", error=str(e))
            return []

    def _extract_story_fields(self, analysis: dict) -> dict:
        """Extract StoryInputs fields from the real tug_of_war analysis dict shape.

        The analysis dict produced by analyze_tug_of_war() stores values at
        nested paths that differ from the flat keys the story builder expects.
        This helper normalises them, falling back to None/0 on missing data.

        Returns a flat dict with keys:
            spot, open_price, previous_close,
            support, resistance,
            verdict_score, verdict_ema, momentum_9m, regime
        """
        # Support / resistance are nested under primary_sr in real tug_of_war output.
        # Fall back to flat keys for synthetic/test dicts that pre-populate them.
        sr = analysis.get("primary_sr") or {}
        support_dict = sr.get("support") or {}
        resistance_dict = sr.get("resistance") or {}
        support = (
            (support_dict.get("strike") if isinstance(support_dict, dict) else None)
            or analysis.get("support")
        )
        resistance = (
            (resistance_dict.get("strike") if isinstance(resistance_dict, dict) else None)
            or analysis.get("resistance")
        )

        # Verdict score — combined_score is the primary scalar
        verdict_score = (
            analysis.get("combined_score")
            or analysis.get("verdict_score")
            or analysis.get("score")
        )

        # EMA / smoothed score
        verdict_ema = (
            analysis.get("smoothed_score")
            or analysis.get("verdict_ema")
            or analysis.get("ema_score")
        )

        # Momentum (normalise to float, clamp to avoid extreme values)
        momentum_9m = (
            analysis.get("momentum_score")
            or analysis.get("momentum_9m")
            or analysis.get("price_change_pct")
            or 0.0
        )

        # Regime — tug_of_war uses lowercase ("trending_up"); narrative engine
        # expects uppercase ("TRENDING_UP"). Try both paths.
        regime_raw = None
        mr = analysis.get("market_regime")
        if isinstance(mr, dict):
            regime_raw = mr.get("regime")
        if not regime_raw:
            regime_raw = analysis.get("regime")
        regime: str | None = None
        if regime_raw:
            # Normalise: "trending_up" → "TRENDING_UP", already-uppercase pass-through
            regime = regime_raw.upper()

        # open_price / previous_close — not present in the tug_of_war blob.
        # Fetch from candle buffers already loaded by the scheduler.
        open_price: float | None = None
        previous_close: float | None = None
        try:
            if hasattr(self, "_yesterday_nifty_candles") and self._yesterday_nifty_candles:
                last_y = self._yesterday_nifty_candles[-1]
                previous_close = float(last_y.get("close") or 0) or None
            if hasattr(self, "_today_open_price"):
                open_price = self._today_open_price
        except Exception:
            pass

        return {
            "spot": analysis.get("spot_price"),
            "open_price": open_price,
            "previous_close": previous_close,
            "support": support,
            "resistance": resistance,
            "verdict_score": verdict_score,
            "verdict_ema": verdict_ema,
            "momentum_9m": float(momentum_9m or 0),
            "regime": regime,
        }

    def _build_story_and_tiles(self, analysis: dict, data_age_seconds: int = 0):
        """Compose the narrative story and tile states from the latest analysis.

        Returns (story_text_or_None, list_of_tile_dicts).
        """
        from analysis.narrative import (
            StoryInputs, build_story, IHStoryState, IHGroupState, RRStoryState,
        )
        from analysis.tile_state import build_tile_state
        from datetime import datetime
        from dataclasses import asdict

        ih_strategy = self.strategies.get("intraday_hunter") if self.strategies else None
        rr_strategy = self.strategies.get("rally_rider") if self.strategies else None

        ih_state = ih_strategy.story_state() if (ih_strategy and hasattr(ih_strategy, "story_state")) \
            else IHStoryState(state=IHGroupState.WAITING)
        rr_state = rr_strategy.story_state() if (rr_strategy and hasattr(rr_strategy, "story_state")) \
            else RRStoryState(state="waiting")

        now = datetime.now()
        minute_of_day = now.hour * 60 + now.minute

        fields = self._extract_story_fields(analysis)

        inputs = StoryInputs(
            spot=fields["spot"],
            open_price=fields["open_price"],
            previous_close=fields["previous_close"],
            support=fields["support"],
            resistance=fields["resistance"],
            verdict_score=fields["verdict_score"],
            regime=fields["regime"],
            momentum_9m=fields["momentum_9m"],
            minute_of_day=minute_of_day,
            ih_state=ih_state,
            rr_state=rr_state,
            data_age_seconds=data_age_seconds,
        )
        story = build_story(inputs)
        story_text = " ".join(story.sentences) if story.has_content() else None

        tiles = build_tile_state(
            verdict_score=float(fields["verdict_score"] or 0),
            verdict_ema=float(fields["verdict_ema"] or 0),
            spot=float(fields["spot"] or 0),
            support=int(fields["support"] or 0),
            resistance=int(fields["resistance"] or 0),
            momentum_9m=float(fields["momentum_9m"] or 0),
            ih_state=ih_state,
            rr_state=rr_state,
        )
        return story_text, [asdict(t) for t in tiles]

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
                    index_label="NIFTY",
                )
            except Exception as e:
                log.error("CandleBuilder strike rotation failed", error=str(e))

            # [V1] Rotate BANKNIFTY + SENSEX + NIFTY option strikes for IH
            # real LTP. Subscribes ATM±spacing (3 CE + 3 PE per index = 6
            # strikes per index, includes ATM). NIFTY is rotated separately
            # here because the main cycle's rotation above subscribes ATM±50,
            # ATM±100, ATM±150 but NOT ATM itself — IH uses ATM strike for
            # entry sizing, so without this it would skip every NIFTY signal
            # firing at ATM (observed in live: ~75% of NIFTY signals blocked).
            if self._ih_cfg.ENABLED and self._multi_imap is not None:
                self._rotate_ih_index_strikes("NIFTY", spacing=50)
                self._rotate_ih_index_strikes("BANKNIFTY", spacing=100)
                self._rotate_ih_index_strikes("SENSEX", spacing=100)

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

            # Generate narrative + tile payloads for the dashboard
            story_text, tile_payloads = self._build_story_and_tiles(analysis, data_age_seconds=0)

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
                prev_verdict=prev_verdict,  # Store previous verdict for hysteresis analysis
                story_text=story_text,
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
                analysis["nifty_1min_candles"] = self._today_only(
                    self.candle_builder.get_candles("NIFTY", "1min"))
                analysis["nifty_3min_candles"] = self._today_only(
                    self.candle_builder.get_candles("NIFTY", "3min"))
                analysis["ce_candles"] = self._today_only(
                    self.candle_builder.get_candles(ce_label, "3min"))
                analysis["pe_candles"] = self._today_only(
                    self.candle_builder.get_candles(pe_label, "3min"))
                analysis["ce_strike"] = atm - 100
                analysis["pe_strike"] = atm + 100
            except Exception as e:
                log.error("Failed to attach candles to analysis dict", error=str(e))
                analysis.setdefault("nifty_1min_candles", [])
                analysis.setdefault("nifty_3min_candles", [])
                analysis.setdefault("ce_candles", [])
                analysis.setdefault("pe_candles", [])

            # IntradayHunter inputs (only when enabled — keeps unrelated
            # deployments unaffected). Adds BN/SX/HDFC/KOTAK candles, the
            # latest BN/SX spot, and yesterday's NIFTY 1-min candles.
            if self._ih_cfg.ENABLED:
                try:
                    self._attach_ih_inputs(analysis)
                except Exception as e:
                    log.error("Failed to attach IntradayHunter inputs", error=str(e))

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

            # NOTE: IntradayHunter no longer runs in this 3-min cycle.
            # It has its own dedicated 1-min job (_run_intraday_hunter_minute)
            # so it can react to every fresh 1-min candle, not just every
            # third minute.

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
            # datetime objects (not JSON serializable by Flask-SocketIO's
            # default encoder) and the dashboard doesn't consume them anyway.
            # Strategies have already read them by this point.
            if self.socketio:
                emit_payload = {
                    k: v for k, v in analysis.items()
                    if k not in (
                        # Existing NIFTY/RR candle fields
                        "ce_candles", "pe_candles",
                        "nifty_1min_candles", "nifty_3min_candles",
                        # IntradayHunter candle fields (added by _attach_ih_inputs)
                        "banknifty_1min_candles", "sensex_1min_candles",
                        "hdfcbank_1min_candles", "kotakbank_1min_candles",
                        "nifty_yesterday_candles",
                    )
                }
                emit_payload["story_text"] = story_text
                emit_payload["tiles"] = tile_payloads
                self.socketio.emit("oi_update", emit_payload)
                self.socketio.emit("story_update", {"story_text": story_text})
                self.socketio.emit("tiles_update", {"tiles": tile_payloads})
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

        # 1-minute IntradayHunter cycle (only when enabled). Runs at the
        # top of every minute, ~5s after the candle close, so it sees the
        # freshest 1-min OHLC bar. Independent of the 3-min OI fetch loop.
        if self._ih_cfg.ENABLED:
            ih_now = datetime.now()
            ih_start = ih_now.replace(second=5, microsecond=0)
            if ih_start <= ih_now:
                from datetime import timedelta as _td
                ih_start = ih_start + _td(minutes=1)
            self.scheduler.add_job(
                self._run_intraday_hunter_minute,
                trigger=IntervalTrigger(minutes=1, start_date=ih_start),
                id="intraday_hunter_minute",
                name="IntradayHunter 1-min cycle",
                replace_existing=True,
                max_instances=1,        # don't overlap if a cycle runs long
                coalesce=True,          # if we miss a tick, run once not multiple
            )
            log.info("IntradayHunter 1-min job scheduled",
                     first_run=ih_start.strftime("%H:%M:%S"))

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

            # 2b. IntradayHunter needs BN/SX spot + HDFC/KOTAK constituents
            #     (only when IH is enabled — keeps the WS subscription footprint
            #     unchanged for users who haven't opted in).
            if self._ih_cfg.ENABLED:
                for label, token, instr_type in IH_INSTRUMENTS:
                    try:
                        self.candle_builder.register_instrument(
                            label=label, token=token,
                            instr_type=instr_type, intervals=("1min",),
                            index_label=label if instr_type == "index" else "NIFTY",
                        )
                    except Exception as e:
                        log.error("IH instrument register failed",
                                  label=label, token=token, error=str(e))

                # Refresh the multi-instrument-map (downloads NFO + BFO CSVs).
                # Required for live order routing on BN/SX positions.
                if self._multi_imap is not None:
                    try:
                        self._multi_imap.set_access_token(
                            self.kite_fetcher._kite.access_token
                            if hasattr(self.kite_fetcher._kite, "access_token") else ""
                        )
                        ok = self._multi_imap.refresh()
                        log.info("MultiInstrumentMap refreshed",
                                 success=ok, indices=self._multi_imap.labels())
                    except Exception as e:
                        log.error("MultiInstrumentMap refresh failed", error=str(e))

            # 3. Bootstrap candle history (one-time historical_data fetch).
            try:
                self.candle_builder.bootstrap()
            except Exception as e:
                log.error("CandleBuilder bootstrap failed", error=str(e))

            # 4. Pick up any active trades from the DB.
            self.exit_monitor.scan_existing_trades(self.strategies)
            # IH multi-position: register via its own method (uses multi_imap)
            ih = self.strategies.get("intraday_hunter")
            if ih:
                self._register_ih_trades_with_monitor(ih)

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
