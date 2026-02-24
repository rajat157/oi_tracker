# NIFTY OI Tracker v2

A full-stack web dashboard that fetches NIFTY option chain data via Kite Connect every 3 minutes, analyzes open interest using a "tug-of-war" model, and runs four independent automated trading strategies with real-time Telegram alerts.

**v2 is a ground-up rewrite** of the original Flask + SQLite app — now FastAPI + PostgreSQL + Next.js with async everything.

## Stack

| Layer | Tech |
|-------|------|
| Backend | FastAPI, async SQLAlchemy 2.0, PostgreSQL, Alembic |
| Frontend | Next.js 16 (App Router), React 19, Tailwind CSS, shadcn/ui, Zustand, Recharts |
| Real-time | Server-Sent Events (SSE) |
| Scheduling | APScheduler (3-minute heartbeat) |
| Broker | Kite Connect API (data + WebSocket premium monitoring) |
| Alerts | Telegram (httpx async) |
| Testing | pytest + pytest-asyncio |
| Infra | Docker Compose, UV package manager |

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) & Docker Compose
- [Node.js](https://nodejs.org/) >= 20
- [UV](https://docs.astral.sh/uv/) (Python package manager)
- Kite Connect API credentials (for live data)

### 1. Environment setup

```bash
cd nifty-oi-tracker
cp .env.example .env
# Edit .env with your credentials (Kite API, Telegram tokens, Postgres password)
```

### 2. Start database + API

```bash
docker compose up postgres api
```

This starts:
- **PostgreSQL** on `localhost:5432` (database: `nifty_oi`)
- **FastAPI** on `localhost:8000` (with hot reload via `docker-compose.override.yml`)

### 3. Run database migrations

```bash
cd backend
uv run alembic upgrade head
```

### 4. Start frontend

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:3000** in your browser.

### 5. Verify

- Dashboard: http://localhost:3000
- Trades: http://localhost:3000/trades
- Logs: http://localhost:3000/logs
- API health: http://localhost:8000/health
- API docs: http://localhost:8000/docs

## Project Structure

```
nifty-oi-tracker/
├── docker-compose.yml              # Postgres + API + Frontend (prod)
├── docker-compose.override.yml     # Dev overrides (volume mount + reload)
├── .env.example                    # Environment template
│
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app factory + lifespan
│   │   ├── api/v1/                 # Route modules
│   │   │   ├── analysis.py         # GET /analysis/latest, /analysis/history
│   │   │   ├── trades.py           # GET /trades/{strategy}, /trades/{strategy}/stats
│   │   │   ├── market.py           # GET /market/status, POST /market/refresh
│   │   │   ├── kite.py             # GET /kite/status, /kite/login, /kite/callback
│   │   │   ├── logs.py             # GET /logs
│   │   │   └── events.py           # GET /events/stream (SSE)
│   │   ├── models/                 # SQLAlchemy ORM models
│   │   ├── schemas/                # Pydantic request/response schemas
│   │   ├── services/               # Business logic (10 services)
│   │   ├── strategies/             # Trading strategy implementations
│   │   ├── engine/                 # Core OI analysis (pure functions)
│   │   ├── tasks/                  # Scheduled tasks (fetch_and_analyze)
│   │   └── core/                   # Config, constants, dependencies
│   ├── tests/                      # pytest test suite
│   ├── scripts/
│   │   └── migrate_sqlite.py       # v1 SQLite → v2 PostgreSQL migration
│   ├── alembic/                    # Database migrations
│   ├── Dockerfile                  # Multi-stage production build
│   └── pyproject.toml              # UV project config
│
└── frontend/
    ├── app/                        # Next.js App Router pages
    │   ├── page.tsx                # Dashboard (SSE + Zustand)
    │   ├── trades/page.tsx         # Trade history + stats
    │   └── logs/page.tsx           # System log viewer
    ├── components/
    │   ├── dashboard/              # VerdictCard, ScoreGauge, TradeCard, OIChart, MetricsPanel
    │   ├── trades/                 # TradeTable, TradeStats
    │   ├── logs/                   # LogViewer
    │   └── ui/                     # shadcn primitives (card, badge, table, button)
    ├── hooks/useSSE.ts             # SSE connection hook
    ├── stores/dashboard-store.ts   # Zustand global state
    ├── lib/
    │   ├── api.ts                  # API client
    │   └── types.ts                # TypeScript interfaces
    ├── Dockerfile                  # Multi-stage standalone build
    └── next.config.ts              # API proxy + standalone output
```

## Trading Strategies

Four independent strategies run simultaneously (max one trade each per day):

| Strategy | Type | RR | Time Window | Entry Condition |
|----------|------|-----|-------------|-----------------|
| **Iron Pulse** | Buying | 1:1.1 | 11:00-14:00 | Slightly Bullish/Bearish, confidence >= 65% |
| **Selling** | Selling | 1:1 (T1) + 1:2 (T2) | 11:00-14:00 | Verdict-aligned, confidence >= 65%, OTM-1 |
| **Dessert** | Buying | 1:2 | 09:30-14:00 | Contra Sniper or Phantom PUT triggers |
| **Momentum** | Buying | 1:2 | 12:00-14:00 | Strong verdict, confidence >= 85%, CONFIRMED |

All active trades are force-closed at **15:20 IST** (EOD).

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/analysis/latest` | GET | Full dashboard payload (verdict, trades, chart data) |
| `/api/v1/analysis/history` | GET | Historical analysis for charts |
| `/api/v1/trades/{strategy}` | GET | Trade history (paginated: `limit`, `offset`) |
| `/api/v1/trades/{strategy}/stats` | GET | Win rate, avg P&L, total P&L |
| `/api/v1/market/status` | GET | Market open/close status |
| `/api/v1/market/refresh` | POST | Trigger manual data fetch |
| `/api/v1/kite/status` | GET | Kite authentication status |
| `/api/v1/kite/login` | GET | Kite OAuth login URL |
| `/api/v1/kite/callback` | GET | Exchange request token |
| `/api/v1/logs` | GET | System logs (filter: `level`, `component`, `hours`) |
| `/api/v1/events/stream` | GET | SSE stream (analysis_update, trade_update) |
| `/health` | GET | Health check |

Strategy names for `{strategy}`: `iron_pulse`, `selling`, `dessert`, `momentum`

## Database Tables

| Table | Description |
|-------|-------------|
| `oi_snapshots` | Raw option chain data per strike per timestamp |
| `analysis_history` | OI analysis results with verdict + `analysis_blob` (JSONB) |
| `iron_pulse_trades` | Iron Pulse buying trades (1:1 RR, trailing SL) |
| `selling_trades` | Selling trades (dual T1/T2 targets) |
| `dessert_trades` | Dessert trades (Contra Sniper + Phantom PUT) |
| `momentum_trades` | Momentum trades (high-conviction trend) |
| `system_logs` | Structured logs with `details` (JSON) |
| `settings` | Key-value store (Kite tokens, app config) |

## Environment Variables

Copy `.env.example` to `.env` and fill in your values:

| Variable | Required | Description |
|----------|----------|-------------|
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password |
| `DATABASE_URL` | Yes | Async PostgreSQL URL (`postgresql+asyncpg://...`) |
| `TEST_DATABASE_URL` | No | Test database URL (port 5433) |
| `KITE_API_KEY` | Yes | Kite Connect API key |
| `KITE_API_SECRET` | Yes | Kite Connect API secret |
| `KITE_ACCESS_TOKEN` | No | Pre-set access token (or use OAuth flow) |
| `TELEGRAM_BOT_TOKEN` | No | Main Telegram bot for all alerts |
| `TELEGRAM_CHAT_ID` | No | Chat ID(s) for main bot |
| `SELLING_ALERT_BOT_TOKEN` | No | Separate bot for selling alerts |
| `SELLING_ALERT_CHAT_IDS` | No | Chat ID(s) for selling bot |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `INFO`) |
| `ENVIRONMENT` | No | `development` or `production` |
| `SHADOW_MODE` | No | Set `true` to log signals without creating trades or sending alerts |

## Common Commands

### Backend

```bash
cd backend

# Run tests
uv run pytest

# Run specific test file
uv run pytest tests/unit/tasks/ -v

# Run with coverage
uv run pytest --cov=app

# Lint
uv run ruff check .

# Create migration
uv run alembic revision --autogenerate -m "description"

# Apply migrations
uv run alembic upgrade head
```

### Frontend

```bash
cd frontend

# Dev server
npm run dev

# Production build
npm run build

# Lint
npm run lint
```

### Docker

```bash
# Local development (postgres + api, frontend via npm)
docker compose up postgres api

# Start test database
docker compose --profile test up postgres-test

# Production (all services including frontend)
docker compose --profile prod up -d

# Build images
docker compose --profile prod build

# View logs
docker compose logs -f api
```

### Shadow Mode

Run the app without executing real trades — useful for validating signals:

```bash
# Add to .env
SHADOW_MODE=true
```

Signals are logged as `SHADOW: would enter trade` / `SHADOW: would exit trade` but no trades are created and no Telegram alerts are sent.

## Migrating from v1

If you have a v1 SQLite database (`oi_tracker.db`), migrate it to v2 PostgreSQL:

```bash
cd backend

# Ensure postgres is running and migrations are applied
uv run alembic upgrade head

# Install psycopg2 (sync driver for migration script)
uv pip install psycopg2-binary

# Run migration
uv run python scripts/migrate_sqlite.py --sqlite-path ../../oi_tracker.db
```

The script migrates all 6 tables with:
- Naive datetime → IST-aware timestamps
- `analysis_json` TEXT → `analysis_blob` JSONB
- `details` TEXT → JSON
- `trade_setups` → `iron_pulse_trades`
- `sell_trade_setups` → `selling_trades`
- Batch inserts (1000 rows) with progress output

## Architecture

### Data Flow

```
Kite Connect API
       |
  [3-min scheduler]
       |
  fetch_and_analyze()
       |
  ┌────┴────┐
  │ Analysis │──► save to DB ──► SSE event ──► Frontend (Zustand store)
  └────┬────┘
       │
  ┌────┴─────────────────┐
  │ Strategy Evaluation   │
  │ (Iron Pulse, Selling, │──► create/exit trade ──► Telegram alert
  │  Dessert, Momentum)   │
  └──────────────────────┘
       │
  PremiumMonitorService
  (Kite WebSocket — real-time SL/target monitoring)
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| 4 separate trade tables | vs single polymorphic table | Strategy-specific columns, no NULL bloat |
| Analysis blob as JSONB | vs normalized columns | Queryable nested fields, flexible schema |
| SSE over WebSocket | vs Socket.IO | Simpler, no extra dependency, native browser support |
| Zustand | vs Redux/Context | Selector-based subscriptions, no re-render cascade |
| Pure function engine | vs class-based | Zero deps, fully testable OI analysis |
| Standalone Next.js output | vs default build | Minimal Docker image (~100MB vs ~500MB) |
