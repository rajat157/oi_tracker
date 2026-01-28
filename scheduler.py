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
from database import save_snapshot, save_analysis, purge_old_data, get_last_data_date, get_recent_price_trend


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
        """Purge previous day's data at the start of a new market day."""
        today = datetime.now().date()

        # Only purge once per day
        if self.last_purge_date == today:
            return

        # Check if there's old data to purge
        last_data_date = get_last_data_date()
        if last_data_date and last_data_date < today:
            print(f"[{datetime.now()}] Purging data from {last_data_date}...")
            purge_old_data(today)
            print(f"[{datetime.now()}] Old data purged. Starting fresh for {today}")

        self.last_purge_date = today

    def fetch_and_analyze(self, force: bool = False):
        """
        Main job: Fetch data, analyze, save, and broadcast.

        Args:
            force: If True, fetch even if market is closed (for testing)
        """
        # Check and purge old data at start of new day
        self.check_and_purge_old_data()

        # Check if market is open
        if not force and not self.is_market_open():
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

            # Perform analysis with momentum
            analysis = analyze_tug_of_war(
                strikes_data,
                spot_price,
                include_atm=True,
                include_itm=True,
                price_history=price_history
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
                atm_call_oi_change=analysis.get("atm_data", {}).get("call_oi_change", 0),
                atm_put_oi_change=analysis.get("atm_data", {}).get("put_oi_change", 0),
                itm_call_oi_change=analysis.get("itm_call_oi_change", 0),
                itm_put_oi_change=analysis.get("itm_put_oi_change", 0)
            )

            self.last_analysis = analysis

            print(f"[{datetime.now()}] Analysis complete: {analysis['verdict']}")

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
            "message": "Market is OPEN" if is_open else "Market is CLOSED"
        }


if __name__ == "__main__":
    # Test scheduler standalone
    print("Starting OI Scheduler test...")
    scheduler = OIScheduler()

    # Just run once for testing
    scheduler.fetch_and_analyze()

    print("\nTest complete.")
