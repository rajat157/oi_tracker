"""DDL constants for all trade tables.

Extracted from legacy tracker init functions to support
TradeRepository.init_table() without depending on legacy modules.
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

RR_TRADES_DDL = """
CREATE TABLE IF NOT EXISTS rr_trades (
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
    signal_type TEXT DEFAULT 'MC',
    signal_data_json TEXT,
    regime TEXT,
    agent_reasoning TEXT,
    agent_confidence REAL,
    vwap_at_creation REAL,
    vix_at_creation REAL,
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
    trail_stage INTEGER DEFAULT 0,
    trade_number INTEGER DEFAULT 1
)
"""

# Map tracker_type -> (DDL, optional indexes)
ALL_TRADE_SCHEMAS = {
    "scalper": (SCALP_TRADES_DDL, []),
    "rally_rider": (RR_TRADES_DDL, []),
}
