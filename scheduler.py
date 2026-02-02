"""
APScheduler for periodic OI data fetching
Runs every 3 minutes to match NSE update frequency
Only runs during market hours (9:15 AM - 3:30 PM IST, weekdays)
"""

from datetime import datetime, time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from nse_fetcher import NSEFetcher
from oi_analyzer import analyze_tug_of_war
from database import (
    save_snapshot, save_analysis, purge_old_data, get_last_data_date,
    get_recent_price_trend, get_recent_oi_changes, get_previous_strikes_data,
    get_previous_futures_oi
)
from self_learner import get_self_learner
from trade_tracker import get_trade_tracker


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
            headless: Run browser in headless mode
        """
        self.scheduler = BackgroundScheduler()
        self.headless = headless
        self.socketio = socketio
        self.last_analysis = None
        self.is_running = False
        self.last_purge_date = None
        self.last_learning_date = None
        self.self_learner = get_self_learner()
        self.trade_tracker = get_trade_tracker()
        self.force_enabled = False

    def set_force_enabled(self, enabled: bool):
        """Enable/disable force fetch mode for automatic polling."""
        self.force_enabled = enabled

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
            print(f"[{datetime.now()}] Purging data older than {cutoff_date} (90-day retention)...")
            purge_old_data(cutoff_date)
            print(f"[{datetime.now()}] Old data purged. Keeping data from {cutoff_date} onwards.")

        self.last_purge_date = today

    def fetch_and_analyze(self, force: bool = False):
        """
        Main job: Fetch data, analyze, save, and broadcast.

        Args:
            force: If True, fetch even if market is closed (for testing)
        """
        # Check and purge old data at start of new day
        self.check_and_purge_old_data()

        # Check if market is open OR force is enabled
        if not force and not self.force_enabled and not self.is_market_open():
            print(f"[{datetime.now()}] Market is closed. Skipping fetch.")
            return

        print(f"[{datetime.now()}] Fetching OI data...")

        fetcher = None
        try:
            # Create fetcher for this job
            fetcher = NSEFetcher(headless=self.headless)

            # Fetch raw data
            raw_data = fetcher.fetch_option_chain()
            if not raw_data:
                print("Failed to fetch data from NSE")
                return

            # Parse the data
            parsed = fetcher.parse_option_data(raw_data)
            if not parsed:
                print("Failed to parse option chain data")
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
            vix = fetcher.fetch_india_vix() or 0.0
            print(f"India VIX: {vix:.2f}" if vix > 0 else "VIX: Not available")

            # Fetch futures data for cross-validation
            futures_data = fetcher.fetch_futures_data() or {}
            futures_oi = futures_data.get("future_oi", 0)
            futures_basis = futures_data.get("basis", 0.0)

            # Calculate futures OI change from previous stored value
            prev_futures_oi = get_previous_futures_oi()
            if prev_futures_oi > 0 and futures_oi > 0:
                futures_oi_change = futures_oi - prev_futures_oi
            else:
                futures_oi_change = 0

            if futures_oi > 0:
                print(f"Futures OI Change: {futures_oi_change:+,} (prev: {prev_futures_oi:,}, curr: {futures_oi:,})")

            # Perform analysis with all enhanced data
            analysis = analyze_tug_of_war(
                strikes_data,
                spot_price,
                include_atm=True,
                include_itm=True,
                price_history=price_history,
                vix=vix,
                futures_oi_change=futures_oi_change,
                prev_oi_changes=prev_oi_changes,
                prev_strikes_data=prev_strikes_data
            )
            analysis["timestamp"] = timestamp.isoformat()
            analysis["expiry_date"] = current_expiry

            # Save analysis to database
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
                futures_basis=futures_basis
            )

            self.last_analysis = analysis

            print(f"[{datetime.now()}] Analysis complete: {analysis['verdict']}")

            # Trade Tracker: manage persistent trade setups
            # 1. Check and update existing setup status (activation/resolution)
            trade_update = self.trade_tracker.check_and_update_setup(strikes_data, timestamp)
            if trade_update:
                print(f"[TradeTracker] Setup #{trade_update['setup_id']}: "
                      f"{trade_update['previous_status']} -> {trade_update['new_status']}")

            # 2. Cancel PENDING setup if OI direction flipped
            self.trade_tracker.cancel_on_direction_change(analysis["verdict"], timestamp)

            # 3. Expire PENDING setups at market close
            self.trade_tracker.expire_pending_setups(timestamp)

            # 4. Force close ACTIVE trades at market close (3:20 PM)
            self.trade_tracker.force_close_active_trades(timestamp, strikes_data)

            # 5. Create new setup if conditions met (pass price_history for timing checks)
            if self.trade_tracker.should_create_new_setup(analysis, price_history):
                self.trade_tracker.create_setup(analysis, timestamp)

            # 6. Add trade tracker data to analysis for dashboard
            tracker_data = self.trade_tracker.get_stats()
            active_setup_with_pnl = self.trade_tracker.get_active_setup_with_pnl(strikes_data)
            analysis["active_trade"] = active_setup_with_pnl
            analysis["trade_stats"] = tracker_data["stats"]

            # Self-learning: check pending signal outcomes
            resolved = self.self_learner.check_outcomes(spot_price, timestamp)
            for r in resolved:
                status = "CORRECT" if r["was_correct"] else "WRONG"
                print(f"[SelfLearner] Signal resolved: {r['verdict']} -> {status} ({r['profit_loss_pct']:+.2f}%)")

            # Self-learning: record new signal
            learning_result = self.self_learner.process_new_signal(timestamp, analysis)
            if learning_result["signal_id"]:
                print(f"[SelfLearner] Recorded signal #{learning_result['signal_id']} (confidence: {learning_result['confidence']:.0f}%)")

            # Add learning status to analysis for dashboard
            analysis["self_learning"] = {
                "should_trade": learning_result["should_trade"],
                "is_paused": learning_result["is_paused"],
                "ema_accuracy": round(learning_result["ema_accuracy"] * 100, 1),
                "consecutive_errors": learning_result["consecutive_errors"]
            }

            # Run daily learning update at market close
            self._check_daily_learning_update()

            # Broadcast to connected clients
            if self.socketio:
                self.socketio.emit("oi_update", analysis)
                print("Emitted update to clients")

        except Exception as e:
            print(f"Error in fetch_and_analyze: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Always close the fetcher to free browser resources
            if fetcher:
                fetcher.close()

    def start(self, interval_minutes: int = 3):
        """
        Start the scheduler.

        Args:
            interval_minutes: How often to fetch data (default 3 mins)
        """
        if self.is_running:
            print("Scheduler already running")
            return

        # Add the job
        self.scheduler.add_job(
            self.fetch_and_analyze,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="oi_fetcher",
            name="Fetch OI Data",
            replace_existing=True
        )

        # Start scheduler
        self.scheduler.start()
        self.is_running = True
        print(f"Scheduler started - fetching every {interval_minutes} minutes")

        # Run immediately on start
        self.fetch_and_analyze()

    def stop(self):
        """Stop the scheduler."""
        if self.is_running:
            self.scheduler.shutdown()
            self.is_running = False
            print("Scheduler stopped")

    def get_last_analysis(self):
        """Get the most recent analysis result."""
        return self.last_analysis

    def trigger_now(self, force: bool = True):
        """
        Manually trigger a fetch (useful for testing).

        Args:
            force: If True, fetch even if market is closed
        """
        self.fetch_and_analyze(force=force)

    def _check_daily_learning_update(self):
        """Run daily learning update at end of market day."""
        now = datetime.now()
        today = now.date()

        # Only run once per day, after market close
        if self.last_learning_date == today:
            return

        # Run after market close (3:30 PM)
        if now.time() >= time(15, 30):
            print(f"[{now}] Running daily learning update...")
            self.self_learner.update_learning()
            self.last_learning_date = today

            # Reset pause state for next day
            if now.time() >= time(15, 45):
                self.self_learner.reset_for_new_day()

    def get_market_status(self) -> dict:
        """Get current market status information."""
        now = datetime.now()
        is_open = self.is_market_open()

        # Add self-learning status
        learning_status = self.self_learner.get_status()

        return {
            "is_open": is_open,
            "current_time": now.strftime("%H:%M:%S"),
            "market_open": MARKET_OPEN.strftime("%H:%M"),
            "market_close": MARKET_CLOSE.strftime("%H:%M"),
            "day": now.strftime("%A"),
            "message": "Market is OPEN" if is_open else "Market is CLOSED",
            "self_learning": {
                "should_trade": learning_status["signal_tracker"]["should_trade"],
                "ema_accuracy": round(learning_status["signal_tracker"]["ema_tracker"]["ema_accuracy"] * 100, 1),
                "is_paused": learning_status["signal_tracker"]["ema_tracker"]["is_paused"]
            }
        }


if __name__ == "__main__":
    # Test scheduler standalone
    print("Starting OI Scheduler test...")
    scheduler = OIScheduler()

    # Just run once for testing
    scheduler.fetch_and_analyze()

    print("\nTest complete.")
