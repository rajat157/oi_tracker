from enum import Enum


class TradeStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    WON = "WON"
    LOST = "LOST"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class TradeDirection(str, Enum):
    BUY_CALL = "BUY_CALL"
    BUY_PUT = "BUY_PUT"
    SELL_CALL = "SELL_CALL"
    SELL_PUT = "SELL_PUT"


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


class StrategyName(str, Enum):
    IRON_PULSE = "iron_pulse"
    SELLING = "selling"
    DESSERT = "dessert"
    MOMENTUM = "momentum"


class Verdict(str, Enum):
    SLIGHTLY_BULLISH = "Slightly Bullish"
    SLIGHTLY_BEARISH = "Slightly Bearish"
    NEUTRAL = "Neutral"
    BULLISH = "Bullish"
    BEARISH = "Bearish"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
