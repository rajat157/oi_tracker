# NIFTY OI Tracker - Development Guide

## Overview
A Python-based web dashboard that fetches NIFTY option chain data from NSE every 3 minutes and analyzes OI to determine market direction using a "tug-of-war" concept. Includes automated trade tracking for both options buying and selling strategies.

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
│   ├── prediction_repo.py # PredictionRepository
│   └── log_repo.py        # LogRepository
├── kite/                  # Kite Connect API
│   ├── iv.py              # Black-Scholes IV calculation
│   ├── auth.py            # OAuth login flow + token storage
│   ├── instruments.py     # NFO instrument lookup and caching
│   ├── broker.py          # Order placement + GTT
│   └── data.py            # KiteDataFetcher (option chain + futures OI)
├── analysis/              # OI analysis, predictions, pattern detection
│   ├── tug_of_war.py      # Core OI tug-of-war analysis (was oi_analyzer.py)
│   ├── pattern_tracker.py # Premium Momentum (PM) reversal detection
│   ├── v_shape.py         # V-shape recovery detector
│   ├── prediction.py      # Prediction tree engine
│   ├── momentum.py        # Momentum calculation re-exports
│   ├── regime_detector.py # Market regime detection re-exports
│   └── confirmation.py    # Signal confidence re-exports
├── strategies/            # Strategy implementations (extend BaseTracker)
│   ├── momentum.py        # MomentumStrategy (trend-following 1:2 RR)
│   ├── dessert.py         # DessertStrategy (Contra Sniper + Phantom PUT)
│   ├── selling.py         # SellingStrategy (dual T1/T2 targets)
│   ├── scalper.py         # ScalperStrategy (Claude-powered multi-trade/day)
│   ├── scalper_engine.py  # Technical analysis for premium charts (VWAP, S/R, swings)
│   ├── scalper_agent.py   # Claude Code FNO expert agent (subprocess via `claude -p`)
│   ├── pulse_rider.py     # PulseRiderStrategy (CHC-3 price action)
│   └── iron_pulse.py      # IronPulseStrategy (PENDING→ACTIVE lifecycle)
├── monitoring/            # Scheduler + premium monitor
│   ├── scheduler.py       # APScheduler for 3-minute polling + trade orchestration
│   └── premium_monitor.py # Real-time premium monitoring via Kite WebSocket
├── alerts/                # Telegram notification system
│   ├── __init__.py        # Re-exports send_telegram + AlertBroker
│   ├── _legacy.py         # Legacy send_telegram/send_telegram_multi functions
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
│   ├── test_strategies/       # Strategy implementation tests
│   └── test_strategy.py       # Legacy strategy validation tests
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
5. **Strategy Trackers** (`strategies/`) evaluate signals: Iron Pulse, Selling, Dessert, Momentum, PulseRider, Scalper
6. **AlertBroker** (`alerts/broker.py`) subscribes to EventBus events and routes to Telegram
7. **SocketIO** pushes updates to connected dashboard clients

### Trading Strategies

**Three independent strategies run simultaneously (one trade each per day):**

#### 🫀 Iron Pulse (trade_tracker.py) — Bread & Butter
- **WR:** 82% backtested | **RR:** 1:1.1
- Time Window: 11:00 - 14:00 IST
- Verdict-aligned, confidence >= 65%
- Slightly Bullish → BUY CALL | Slightly Bearish → BUY PUT
- SL: -20% | Target: +22%

#### 💰 Selling (selling_tracker.py) — Dual Target
- **WR:** 83% backtested | **RR:** 1:1 (T1) + 1:2 (T2)
- Time Window: 11:00 - 14:00 IST
- Verdict-aligned, confidence >= 65%, OTM-1 strike
- SL: +25% premium rise | T1: -25% drop (notify) | T2: -50% drop (auto-exit)
- EOD exit: 15:20

#### 🍰 Dessert (dessert_tracker.py) — Premium 1:2 RR
- **Combined WR:** 86% backtested | **RR:** 1:2
- Time Window: 9:30 - 14:00 IST
- One per day, first strategy to trigger wins:
  - 🎯 **Contra Sniper:** BUY PUT when verdict Bullish + IV skew < 1 + below max pain (100% WR, 3 trades)
  - 🔮 **Phantom PUT:** BUY PUT when conf < 50% + IV skew < 0 + spot rising 30m (83% WR, 6 trades)
- SL: -25% | Target: +50%

#### Scalper Agent (scalper_tracker.py + scalper_agent.py) — Claude-Powered
- **WR:** 53% backtested (mechanical) | **RR:** 1:1
- Time Window: 9:30 - 14:30 IST
- Multiple trades per day (max 5, 6-min cooldown)
- Strikes: 2 below ATM for CE, 2 above ATM for PE (slightly ITM)
- Pre-filter: Python engine detects VWAP breakout / support bounce / momentum burst
- Signal: Claude Code subprocess (`claude -p`) analyzes full premium chart
- SL: -10% (technical level) | Target: +10%
- Real-time monitoring via WebSocket between 3-min cycles

### Telegram Alerts
- **Main bot** (`TELEGRAM_BOT_TOKEN`): All alerts to Mason
- **External bot** (`SELLING_ALERT_BOT_TOKEN`): Selling alerts only to external users
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
| `/api/trades` | GET | Buying trade history (paginated) |
| `/api/sell-trades` | GET | Selling trade history |
| `/api/sell-stats` | GET | Selling trade statistics |
| `/api/dessert-trades` | GET | Dessert trade history |
| `/api/dessert-stats` | GET | Dessert trade statistics |
| `/api/learning-report` | GET | Self-learning insights |
| `/api/learning-status` | GET | Detailed learning status |
| `/api/prediction-tree` | GET | Current prediction tree state (path, node, signal) |
| `/api/prediction-stats` | GET | Prediction accuracy statistics |
| `/api/scalp-trades` | GET | Scalper trade history |
| `/api/scalp-stats` | GET | Scalper trade statistics |
| `/api/logs` | GET | System logs with filtering |

## Database Tables

| Table | Purpose |
|-------|---------|
| `oi_snapshots` | Raw option chain data per strike |
| `analysis_history` | OI analysis results with verdicts |
| `trade_setups` | Buying trade lifecycle |
| `sell_trade_setups` | Selling trade lifecycle (with T1/T2) |
| `dessert_trades` | Dessert trade lifecycle |
| `signal_outcomes` | Signal accuracy tracking |
| `confidence_accuracy` | Confidence bucket performance |
| `verdict_accuracy` | Verdict type performance |
| `learned_weights` | Self-learner adaptive weights |
| `pm_history` | Premium momentum history |
| `detected_patterns` | PM reversal patterns |
| `prediction_nodes` | Prediction tree nodes (3 scenarios per candle) |
| `prediction_paths` | Prediction path tracking (depth, conviction, contrarian weight) |
| `scalp_trades` | Scalper trade lifecycle (multi-trade/day) |
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
- UV package manager
- Claude Code CLI (`claude` on PATH) — required for Scalper Agent

## Notes
- NSE rate limits: 3-minute interval respects this
- Database: SQLite file `oi_tracker.db` in project root
- All three strategies are INDEPENDENT (one of each per day)
- Telegram alerts: main bot for all (Mason), separate bot for selling (external users)
- All sensitive tokens/IDs in `.env` (gitignored)
- Server restart required for Python code changes