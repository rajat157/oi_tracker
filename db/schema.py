"""DDL constants for all trade tables.

Extracted from legacy tracker init functions to support
TradeRepository.init_table() without depending on legacy modules.
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
    trade_number INTEGER DEFAULT 1,
    order_id TEXT,
    gtt_trigger_id INTEGER,
    actual_fill_price REAL,
    is_paper INTEGER DEFAULT 0,
    soft_sl_premium REAL DEFAULT 0
)
"""

IH_TRADES_DDL = """
CREATE TABLE IF NOT EXISTS ih_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_group_id TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    index_label TEXT NOT NULL,            -- 'NIFTY' / 'BANKNIFTY' / 'SENSEX'
    direction TEXT NOT NULL,              -- 'BUY' or 'SELL'
    strike INTEGER NOT NULL,
    option_type TEXT NOT NULL,            -- 'CE' or 'PE'
    qty INTEGER NOT NULL,
    entry_premium REAL NOT NULL,
    sl_premium REAL NOT NULL,
    target_premium REAL NOT NULL,
    spot_at_creation REAL NOT NULL,
    iv_at_creation REAL,
    vix_at_creation REAL,
    trigger TEXT,                         -- 'E1' / 'E2' / 'E3'
    day_bias_score REAL,                  -- score at signal time
    notes TEXT,
    status TEXT DEFAULT 'ACTIVE',         -- ACTIVE / WON / LOST
    resolved_at DATETIME,
    exit_premium REAL,
    exit_reason TEXT,                     -- SL_HIT / TGT_HIT / TIME_EXIT / EOD_FORCE
    profit_loss_pct REAL,
    profit_loss_rs REAL,
    max_premium_reached REAL,
    min_premium_reached REAL,
    last_checked_at DATETIME,
    last_premium REAL,
    order_id TEXT,
    gtt_trigger_id INTEGER,
    actual_fill_price REAL,
    is_paper INTEGER DEFAULT 1            -- default PAPER until BN/SX broker integration ready
)
"""

IH_TRADES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ih_signal_group ON ih_trades(signal_group_id)",
    "CREATE INDEX IF NOT EXISTS idx_ih_status ON ih_trades(status)",
    "CREATE INDEX IF NOT EXISTS idx_ih_created_at ON ih_trades(created_at)",
]

# Map tracker_type -> (DDL, optional indexes)
ALL_TRADE_SCHEMAS = {
    "rally_rider": (RR_TRADES_DDL, []),
    "intraday_hunter": (IH_TRADES_DDL, IH_TRADES_INDEXES),
}
