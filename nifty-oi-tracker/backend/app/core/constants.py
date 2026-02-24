"""Market hours, strategy parameters, and other constants."""

from datetime import time

# Market hours (IST)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
FORCE_CLOSE_TIME = time(15, 20)

# Strike intervals
NIFTY_STEP = 50

# OI Analysis
DEAD_ZONE = 10
OI_CHANGE_WEIGHT = 0.85
TOTAL_OI_WEIGHT = 0.15
NUM_OTM_STRIKES = 3

# Iron Pulse (Buying) Strategy
IRON_PULSE_TIME_START = time(11, 0)
IRON_PULSE_TIME_END = time(14, 0)
IRON_PULSE_SL_PCT = 20.0
IRON_PULSE_TARGET_PCT = 22.0
IRON_PULSE_MIN_CONFIDENCE = 65.0
IRON_PULSE_TRAILING_SL_PCT = 15.0

# Selling Strategy
SELLING_TIME_START = time(11, 0)
SELLING_TIME_END = time(14, 0)
SELLING_MIN_CONFIDENCE = 65.0
SELLING_SL_PCT = 25.0
SELLING_TARGET1_PCT = 25.0
SELLING_TARGET2_PCT = 50.0
SELLING_OTM_OFFSET = 1
SELLING_MIN_PREMIUM = 5.0

# Dessert Strategy
DESSERT_TIME_START = time(9, 30)
DESSERT_TIME_END = time(14, 0)
DESSERT_SL_PCT = 25.0
DESSERT_TARGET_PCT = 50.0
DESSERT_MIN_PREMIUM = 5.0

# Momentum Strategy
MOMENTUM_MIN_CONFIDENCE = 85.0

# IV-based Dynamic SL
IV_SL_TIERS = [
    (12.0, 15.0),
    (15.0, 18.0),
    (18.0, 20.0),
    (22.0, 22.0),
    (float("inf"), 25.0),
]

# Data retention
DATA_RETENTION_DAYS = 90
