# NIFTY OI Tracker - Development Guide

## Overview
A Python-based web dashboard that fetches NIFTY option chain data from NSE every 3 minutes and analyzes OI to determine market direction using a "tug-of-war" concept. Includes a Claude-powered Rally Rider agent for automated trade tracking.

## Project Structure
```
oi_tracker/
├── app.py                 # Flask web server + main entry point
├── config.py              # Centralized strategy constants (all configs)
├── core/                  # Domain objects & shared infrastructure
│   ├── trade.py           # TradeStatus/Direction enums, TradeSignal/Result/ActiveTrade
│   ├── analysis.py        # AnalysisResult dataclass (wraps tug-of-war dict)
│   ├── events.py          # EventBus pub/sub (TRADE_CREATED/EXITED/UPDATED)
│   ├── base_tracker.py    # ABC for all strategy trackers
│   └── logger.py          # Centralized structured logging with DB persistence
├── db/                    # Repository pattern + legacy DB functions
│   ├── connection.py      # get_connection() + init_db()
│   ├── base_repo.py       # BaseRepository with _execute/_fetch helpers
│   ├── legacy.py          # Legacy SQLite functions (was database.py)
│   ├── settings_repo.py   # Key-value settings (get_setting/set_setting)
│   ├── trade_repo.py      # Generic TradeRepository (works across all trade tables)
│   ├── snapshot_repo.py   # SnapshotRepository
│   ├── analysis_repo.py   # AnalysisRepository
│   ├── signal_repo.py     # SignalRepository
│   └── log_repo.py        # LogRepository
├── kite/                  # Kite Connect API
│   ├── iv.py              # Black-Scholes IV calculation
│   ├── auth.py            # OAuth login flow + token storage
│   ├── instruments.py     # NFO instrument lookup and caching
│   ├── broker.py          # Raw Kite API: place_order, place_gtt_oco, modify_gtt, delete_gtt
│   ├── order_executor.py  # Unified OrderExecutor: entry + GTT OCO + trailing SL + exit
│   └── data.py            # KiteDataFetcher (option chain + futures OI)
├── analysis/              # OI analysis, predictions, pattern detection
│   ├── tug_of_war.py      # Core OI tug-of-war analysis (was oi_analyzer.py)
│   ├── pattern_tracker.py # Premium Momentum (PM) reversal detection
│   └── v_shape.py         # V-shape recovery detector
├── strategies/            # Strategy implementations (extend BaseTracker)
│   ├── premium_engine.py  # Technical analysis for premium charts (VWAP, S/R, swings)
│   ├── rr_strategy.py     # RRStrategy (regime-adaptive, Claude-agent-powered)
│   ├── rr_engine.py       # RR signal detection (MC/MOM/VWAP) + regime classification
│   └── rr_agent.py        # RR Claude subprocess with regime-aware prompt
├── monitoring/            # Scheduler + premium monitor
│   ├── scheduler.py       # APScheduler for 3-minute polling + trade orchestration
│   └── premium_monitor.py # Real-time premium monitoring via Kite WebSocket
├── alerts/                # Telegram notification system
│   ├── __init__.py        # Re-exports send_telegram + AlertBroker
│   ├── _legacy.py         # Legacy send_telegram functions
│   ├── telegram.py        # TelegramChannel (new OOP wrapper)
│   └── broker.py          # AlertBroker (EventBus → Telegram routing)
├── templates/
│   └── dashboard.html     # Web dashboard with live updates
├── static/
│   ├── styles.css         # Dashboard styling
│   └── chart.js           # Chart.js for OI visualization
├── tests/                 # Test files
│   ├── test_config.py         # Config class tests
│   ├── test_core_trade.py     # Trade domain object tests
│   ├── test_core_analysis.py  # AnalysisResult tests
│   ├── test_events.py         # EventBus tests
│   ├── test_base_tracker.py   # BaseTracker ABC tests
│   ├── test_db_repos.py       # Repository pattern tests
│   └── test_strategies/       # Strategy implementation tests
├── scripts/               # Operational tools
│   ├── check_live_stats.py    # Win rate & P&L stats viewer
│   ├── check_trades.py        # Trade history table viewer
│   ├── daily_check.bat        # Daily monitoring batch script
│   ├── exchange_token.py      # Kite token exchange (daily auth)
│   ├── explore_db.py          # Database exploration utility
│   ├── migrate_kite_token.py  # Token storage migration
│   └── verify_kite.py         # Kite auth verification
├── docs/                  # Implementation documentation (archived)
├── pyproject.toml         # UV project configuration
├── README.md              # Project overview
└── CLAUDE.md              # This file
```

## Quick Start

### Run the application
```bash
uv run python app.py
```
Then open http://localhost:5000 in your browser.

## Architecture

### Data Flow
1. **Scheduler** triggers every 3 minutes
2. **Kite Data Fetcher** retrieves option chain data via Kite Connect API
3. **OI Analyzer** performs tug-of-war analysis
4. **Database** stores snapshots and analysis results
5. **Rally Rider** (`strategies/rr_strategy.py`) regime-adaptive rally catcher with Claude agent
7. **AlertBroker** (`alerts/broker.py`) subscribes to EventBus events and routes to Telegram
7. **SocketIO** pushes updates to connected dashboard clients

### Trading Strategy

#### Rally Rider (rr_strategy.py + rr_engine.py + rr_agent.py) — Regime-Adaptive Claude Agent
- **WR:** 60.2% backtested (300 days, 727 trades) | **PF:** 1.90 | Passes all 15 months
- 6 market regimes: HIGH_VOL_DOWN, HIGH_VOL_UP, LOW_VOL, NORMAL, TRENDING_DOWN, TRENDING_UP
- Per-regime parameters: time window, direction filter, SL/TGT pts, max trades, cooldown
- 5 signal types: MC (rally+pullback), MOM (4 consecutive spot 3-min candles), PMOM (4 consecutive option 3-min OHLC closes from Kite historical_data), NMOM (4 consecutive NIFTY 1-min OHLC closes from Kite historical_data), VWAP (spot crosses VWAP)
- Agent prompt includes: NIFTY 3-min OHLC + NIFTY 1-min OHLC + hybrid option premium chart (real OHLC + oi_snapshots IV/OI)
- Claude agent confirms/rejects mechanical signals with regime context + premium chart
- Tick rounding: all premiums at 0.05 increments
- 2-stage trailing stop (+10%→4%, +15%→10%)
- Time exits: regime-specific max_hold (flat), 45m forced, 15:15 EOD
- Max 3 trades/day (regime may narrow), 8-min cooldown (regime may adjust)
- Strikes: ATM - 100 for CE, ATM + 100 for PE (2 ITM)

### Live Trading (OrderExecutor)
- **Master switch**: `LIVE_TRADING_ENABLED=false` in `.env` (default: paper trading)
- **Lots**: `LIVE_TRADING_LOTS=1` (1 lot = 65 qty)
- **Flow**: Entry order (MARKET/LIMIT) → GTT OCO (SL + target) → trailing SL modifies GTT → exit cancels GTT
- **Tick rounding**: All prices at 0.05 increments (`round_to_tick` in `kite/order_executor.py`)
- **Unified**: Any strategy uses the same `OrderExecutor` instance
- **Error handling**: If order fails, paper trade continues. If GTT fails, WebSocket still monitors.
- **Schema**: `order_id` + `gtt_trigger_id` columns on trade tables

### Telegram Alerts
- **Main bot** (`TELEGRAM_BOT_TOKEN`): All alerts to Mason
- Chat IDs configured in `.env` (comma-separated for multiple recipients)

### Tug-of-War Analysis Logic
- Find ATM strike closest to spot price
- Analyze OI in 4 zones: OTM Calls, OTM Puts, ITM Calls, ITM Puts
- Force = Conviction x (85% OI Change + 15% Total OI)
- Verdict from -100 to +100 with hysteresis (dead zone ±10)
- Futures OI confirmation filter (68% vs 43% accuracy)

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main dashboard |
| `/trades` | GET | Trade history page |
| `/api/latest` | GET | Latest OI analysis (JSON) |
| `/api/history` | GET | Historical analysis for charts |
| `/api/refresh` | GET | Trigger manual data fetch |
| `/api/market-status` | GET | Market open/close status |
| `/api/rr-trades` | GET | Rally Rider trade history |
| `/api/rr-stats` | GET | Rally Rider trade statistics |
| `/api/logs` | GET | System logs with filtering |

## Database Tables

| Table | Purpose |
|-------|---------|
| `oi_snapshots` | Raw option chain data per strike |
| `analysis_history` | OI analysis results with verdicts |
| `signal_outcomes` | Signal accuracy tracking |
| `pm_history` | Premium momentum history |
| `detected_patterns` | PM reversal patterns |
| `rr_trades` | Rally Rider trade lifecycle (regime-adaptive, max 3/day) |
| `nifty_history` | NIFTY 50 3-min OHLC candles (300 days) |
| `vix_history` | India VIX 3-min candles (300 days) |
| `system_logs` | Structured log storage |

## Dependencies
- flask, flask-socketio — Web framework + WebSocket
- kiteconnect — Kite Connect API for market data + order placement
- apscheduler — Background job scheduling
- eventlet — Async worker for SocketIO
- requests — Telegram API calls

## Requirements
- Python 3.11+
- Kite Connect API credentials (in `.env`)
- For live trading: `LIVE_TRADING_ENABLED=true` + `LIVE_TRADING_LOTS=1` in `.env`
- UV package manager
- Claude Code CLI (`claude` on PATH) — required for Rally Rider Agent

## Notes
- NSE rate limits: 3-minute interval respects this
- Database: SQLite file `oi_tracker.db` in project root
- Telegram alerts: main bot for all (Mason)
- All sensitive tokens/IDs in `.env` (gitignored)
- Server restart required for Python code changes
