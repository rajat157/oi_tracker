"""DDL constants for all trade tables.

Extracted from legacy tracker init functions to support
TradeRepository.init_table() without depending on legacy modules.
"""

TRADE_SETUPS_DDL = """
CREATE TABLE IF NOT EXISTS trade_setups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Creation
    created_at DATETIME NOT NULL,
    direction TEXT NOT NULL,
    strike INTEGER NOT NULL,
    option_type TEXT NOT NULL,
    moneyness TEXT NOT NULL,
    entry_premium REAL NOT NULL,
    sl_premium REAL NOT NULL,
    target1_premium REAL NOT NULL,
    target2_premium REAL,
    risk_pct REAL NOT NULL,
    spot_at_creation REAL NOT NULL,
    verdict_at_creation TEXT NOT NULL,
    signal_confidence REAL NOT NULL,
    iv_at_creation REAL DEFAULT 0.0,
    expiry_date TEXT NOT NULL,
    -- Status
    status TEXT NOT NULL DEFAULT 'PENDING',
    -- Activation
    activated_at DATETIME,
    activation_premium REAL,
    -- Resolution
    resolved_at DATETIME,
    exit_premium REAL,
    hit_sl BOOLEAN DEFAULT 0,
    hit_target BOOLEAN DEFAULT 0,
    profit_loss_pct REAL,
    profit_loss_points REAL,
    -- Tracking
    max_premium_reached REAL,
    min_premium_reached REAL,
    last_checked_at DATETIME,
    last_premium REAL
)
"""

TRADE_SETUPS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_trade_setups_status ON trade_setups(status)",
    "CREATE INDEX IF NOT EXISTS idx_trade_setups_created ON trade_setups(created_at)",
]

SELL_TRADE_SETUPS_DDL = """
CREATE TABLE IF NOT EXISTS sell_trade_setups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME NOT NULL,
    direction TEXT NOT NULL,
    strike INTEGER NOT NULL,
    option_type TEXT NOT NULL,
    entry_premium REAL NOT NULL,
    sl_premium REAL NOT NULL,
    target_premium REAL NOT NULL,
    target2_premium REAL,
    spot_at_creation REAL NOT NULL,
    verdict_at_creation TEXT NOT NULL,
    signal_confidence REAL,
    iv_at_creation REAL,
    status TEXT DEFAULT 'ACTIVE',
    resolved_at DATETIME,
    exit_premium REAL,
    exit_reason TEXT,
    profit_loss_pct REAL,
    max_premium_reached REAL,
    min_premium_reached REAL,
    last_checked_at DATETIME,
    last_premium REAL,
    t1_hit INTEGER DEFAULT 0,
    t1_hit_at DATETIME
)
"""

DESSERT_TRADES_DDL = """
CREATE TABLE IF NOT EXISTS dessert_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME NOT NULL,
    strategy_name TEXT NOT NULL,
    direction TEXT NOT NULL,
    strike INTEGER NOT NULL,
    option_type TEXT NOT NULL,
    entry_premium REAL NOT NULL,
    sl_premium REAL NOT NULL,
    target_premium REAL NOT NULL,
    spot_at_creation REAL NOT NULL,
    verdict_at_creation TEXT NOT NULL,
    signal_confidence REAL,
    iv_skew_at_creation REAL,
    vix_at_creation REAL,
    max_pain_at_creation REAL,
    spot_move_30m REAL,
    status TEXT DEFAULT 'ACTIVE',
    resolved_at DATETIME,
    exit_premium REAL,
    exit_reason TEXT,
    profit_loss_pct REAL,
    max_premium_reached REAL,
    min_premium_reached REAL,
    last_checked_at DATETIME,
    last_premium REAL
)
"""

MOMENTUM_TRADES_DDL = """
CREATE TABLE IF NOT EXISTS momentum_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME NOT NULL,
    strategy_name TEXT NOT NULL DEFAULT 'Momentum',
    direction TEXT NOT NULL,
    strike INTEGER NOT NULL,
    option_type TEXT NOT NULL,
    entry_premium REAL NOT NULL,
    sl_premium REAL NOT NULL,
    target_premium REAL NOT NULL,
    spot_at_creation REAL NOT NULL,
    verdict_at_creation TEXT NOT NULL,
    signal_confidence REAL,
    iv_skew_at_creation REAL,
    vix_at_creation REAL,
    combined_score REAL,
    confirmation_status TEXT,
    status TEXT DEFAULT 'ACTIVE',
    resolved_at DATETIME,
    exit_premium REAL,
    exit_reason TEXT,
    profit_loss_pct REAL,
    max_premium_reached REAL,
    min_premium_reached REAL,
    last_checked_at DATETIME,
    last_premium REAL
)
"""

PA_TRADES_DDL = """
CREATE TABLE IF NOT EXISTS pa_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME NOT NULL,
    direction TEXT NOT NULL,
    strike INTEGER NOT NULL,
    option_type TEXT NOT NULL,
    entry_premium REAL NOT NULL,
    sl_premium REAL NOT NULL,
    target_premium REAL NOT NULL,
    spot_at_creation REAL NOT NULL,
    verdict_at_creation TEXT,
    signal_confidence REAL,
    iv_skew_at_creation REAL,
    vix_at_creation REAL,
    chc_strength REAL,
    status TEXT DEFAULT 'ACTIVE',
    resolved_at DATETIME,
    exit_premium REAL,
    exit_reason TEXT,
    profit_loss_pct REAL,
    max_premium_reached REAL,
    min_premium_reached REAL,
    last_checked_at DATETIME,
    last_premium REAL
)
"""

SCALP_TRADES_DDL = """
CREATE TABLE IF NOT EXISTS scalp_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME NOT NULL,
    direction TEXT NOT NULL,
    strike INTEGER NOT NULL,
    option_type TEXT NOT NULL,
    entry_premium REAL NOT NULL,
    sl_premium REAL NOT NULL,
    target_premium REAL NOT NULL,
    spot_at_creation REAL NOT NULL,
    verdict_at_creation TEXT,
    signal_confidence REAL,
    vix_at_creation REAL,
    iv_at_creation REAL,
    vwap_at_creation REAL,
    agent_reasoning TEXT,
    status TEXT DEFAULT 'ACTIVE',
    resolved_at DATETIME,
    exit_premium REAL,
    exit_reason TEXT,
    profit_loss_pct REAL,
    max_premium_reached REAL,
    min_premium_reached REAL,
    last_checked_at DATETIME,
    last_premium REAL,
    trade_number INTEGER DEFAULT 1
)
"""

# Map tracker_type -> (DDL, optional indexes)
ALL_TRADE_SCHEMAS = {
    "iron_pulse": (TRADE_SETUPS_DDL, TRADE_SETUPS_INDEXES),
    "selling": (SELL_TRADE_SETUPS_DDL, []),
    "dessert": (DESSERT_TRADES_DDL, []),
    "momentum": (MOMENTUM_TRADES_DDL, []),
    "pulse_rider": (PA_TRADES_DDL, []),
    "scalper": (SCALP_TRADES_DDL, []),
}
