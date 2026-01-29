# NIFTY OI Tracker

A real-time Options Open Interest (OI) analysis dashboard for NIFTY index, using a "tug-of-war" concept to determine market direction.

## Features

- **Real-time OI Analysis**: Fetches NSE option chain data every 3 minutes
- **Tug-of-War Visualization**: Compares Call vs Put OI to gauge market sentiment
- **Multi-Zone Analysis**: Tracks OTM, ATM, and ITM strikes separately
- **Volume Weighting**: Prioritizes high-activity strikes in calculations
- **Price Momentum**: Incorporates recent price trends to filter false signals
- **Live Dashboard**: Auto-updating web interface with WebSocket support
- **Historical Charts**: Track OI changes and sentiment over time

## Quick Start

```bash
# Install dependencies
uv sync

# Run the application
uv run python app.py
```

Then open http://localhost:5000 in your browser.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Scheduler  │────>│ NSE Fetcher │────>│ OI Analyzer │
│  (3 min)    │     │ (Selenium)  │     │ (Tug-of-War)│
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
       ┌───────────────────────────────────────┘
       │
       v
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Database   │<────│   Flask     │────>│  Dashboard  │
│  (SQLite)   │     │  SocketIO   │     │  (Browser)  │
└─────────────┘     └─────────────┘     └─────────────┘
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Main dashboard |
| `GET /api/latest` | Latest OI analysis |
| `GET /api/history` | Historical analysis data |
| `GET /api/refresh` | Trigger manual data refresh |
| `GET /api/market-status` | Current market status |

## Analysis Logic

The "tug-of-war" analysis works by:

1. Finding the ATM (At The Money) strike closest to spot price
2. Analyzing OI changes in multiple zones:
   - **OTM Calls** (above spot): High OI = Bearish pressure
   - **OTM Puts** (below spot): High OI = Bullish pressure
   - **ATM/ITM**: Optional, weighted differently
3. Calculating a composite score from -100 (bearish) to +100 (bullish)
4. Applying volume weighting and momentum filters

## Project Structure

```
oi_tracker/
├── app.py              # Flask web server (entry point)
├── database.py         # SQLite storage
├── nse_fetcher.py      # NSE API data fetching
├── oi_analyzer.py      # OI analysis logic
├── scheduler.py        # APScheduler for polling
├── static/             # CSS and JavaScript
├── templates/          # HTML templates
├── tests/              # Test files
├── scripts/            # Utility scripts
└── docs/               # Implementation documentation
```

## Requirements

- Python 3.11+
- Chrome browser (for Selenium WebDriver)
- UV package manager

## Configuration

The scheduler runs every 3 minutes by default to respect NSE rate limits. This can be adjusted in `app.py`:

```python
oi_scheduler.start(interval_minutes=3)
```

## License

MIT
