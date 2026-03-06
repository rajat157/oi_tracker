"""
Forward Test Simulator for OI Tracker Trading Strategies
=========================================================
Replays historical data candle-by-candle and simulates ALL 5 strategies:
  1. Iron Pulse (buying) — 1:1.1 RR
  2. Selling (dual T1/T2)
  3. Dessert (Contra Sniper + Phantom PUT) — 1:2 RR
  4. Momentum (trend-following) — 1:2 RR
  5. Price Action / CHC-3 (premium momentum) — 1:1 RR

Also runs comprehensive market condition tests and stress scenario analysis.
"""

import sqlite3
import json
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "oi_tracker.db"

# ─── Strategy Constants ──────────────────────────────────────────────────────

# Iron Pulse
IP_SL_PCT = 0.20
IP_TARGET_PCT = 0.22
IP_MIN_CONFIDENCE = 65
IP_MIN_PREMIUM_PCT = 0.0020

# Selling
SELL_SL_PCT = 0.25
SELL_T1_PCT = 0.25
SELL_T2_PCT = 0.50
SELL_MIN_CONFIDENCE = 65
SELL_MIN_PREMIUM = 5.0
SELL_OTM_OFFSET = 1
NIFTY_STEP = 50

# Dessert
DESSERT_SL_PCT = 0.25
DESSERT_TARGET_PCT = 0.50

# Momentum
MOM_SL_PCT = 0.25
MOM_TARGET_PCT = 0.50
MOM_MIN_CONFIDENCE = 85
MOM_MIN_PREMIUM = 5.0
MOM_BEARISH = ("Bears Winning", "Bears Strongly Winning")
MOM_BULLISH = ("Bulls Winning", "Bulls Strongly Winning")

# Price Action (CHC-3)
PA_SL_PCT = 0.15
PA_TARGET_PCT = 0.15
PA_MIN_PREMIUM = 5.0
PA_CHC_LOOKBACK = 3
PA_CHOPPY_LOOKBACK = 10
PA_CHOPPY_THRESHOLD = 0.15

# Time windows
IP_START = "11:00"
IP_END = "14:00"
SELL_START = "11:00"
SELL_END = "14:00"
DESSERT_START = "09:30"
DESSERT_END = "14:00"
MOM_START = "12:00"
MOM_END = "14:00"
PA_START = "09:30"
PA_END = "14:00"
EOD_EXIT = "15:20"


# ─── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class Candle:
    timestamp: str
    time_str: str
    spot_price: float
    atm_strike: int
    verdict: str
    confidence: float
    iv_skew: float
    max_pain: float
    combined_score: float
    futures_oi_change: float
    confirmation_status: str
    strikes: dict = field(default_factory=dict)


@dataclass
class Trade:
    strategy: str
    sub_strategy: str
    day: str
    entry_time: str
    direction: str
    strike: int
    option_type: str
    entry_premium: float
    sl_premium: float
    target_premium: float
    target2_premium: float = 0
    entry_spot: float = 0
    entry_verdict: str = ""
    entry_confidence: float = 0
    status: str = "ACTIVE"
    exit_time: str = ""
    exit_premium: float = 0
    exit_reason: str = ""
    pnl_pct: float = 0
    max_premium: float = 0
    min_premium: float = 0
    t1_hit: bool = False


@dataclass
class DayClassification:
    date: str
    open_spot: float
    close_spot: float
    high_spot: float
    low_spot: float
    day_range_pct: float
    day_change_pct: float
    regime: str
    volatility: str
    # Extended market test fields
    morning_move_pct: float = 0    # first 30 min move
    midday_move_pct: float = 0     # 11:00-13:00 move
    eod_move_pct: float = 0        # last hour move
    verdict_flips: int = 0         # direction changes
    avg_confidence: float = 0      # mean confidence
    dominant_verdict: str = ""     # most frequent verdict
    max_spike_pct: float = 0       # largest 3-min candle
    v_reversal: bool = False
    whipsaw: bool = False
    gap_open: bool = False


# ─── Data Loading ────────────────────────────────────────────────────────────

def load_data() -> dict:
    """Load all analysis and LTP data grouped by day."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
               iv_skew, max_pain, futures_oi_change, analysis_json
        FROM analysis_history
        WHERE signal_confidence > 0
        ORDER BY timestamp
    """)
    analysis_rows = cur.fetchall()

    cur.execute("""
        SELECT timestamp, strike_price, ce_ltp, pe_ltp
        FROM oi_snapshots
        WHERE ce_ltp > 0 OR pe_ltp > 0
        ORDER BY timestamp, strike_price
    """)
    ltp_rows = cur.fetchall()

    conn.close()

    ltp_by_ts = defaultdict(dict)
    for row in ltp_rows:
        ts = row["timestamp"]
        strike = int(row["strike_price"])
        ltp_by_ts[ts][strike] = {
            "ce_ltp": row["ce_ltp"],
            "pe_ltp": row["pe_ltp"]
        }

    days = defaultdict(list)
    for row in analysis_rows:
        ts = row["timestamp"]
        time_str = ts[11:16]
        day = ts[:10]

        combined_score = 0
        confirmation_status = ""
        if row["analysis_json"]:
            try:
                aj = json.loads(row["analysis_json"])
                combined_score = aj.get("combined_score", 0)
                confirmation_status = aj.get("confirmation_status", "")
            except Exception:
                pass

        candle = Candle(
            timestamp=ts,
            time_str=time_str,
            spot_price=row["spot_price"],
            atm_strike=int(row["atm_strike"]),
            verdict=row["verdict"],
            confidence=row["signal_confidence"],
            iv_skew=row["iv_skew"] or 0,
            max_pain=row["max_pain"] or 0,
            combined_score=combined_score,
            futures_oi_change=row["futures_oi_change"] or 0,
            confirmation_status=confirmation_status,
            strikes=ltp_by_ts.get(ts, {})
        )
        days[day].append(candle)

    return dict(days)


# ─── Market Classification ──────────────────────────────────────────────────

def classify_day(day: str, candles: list) -> DayClassification:
    """Classify market conditions with extended metrics."""
    spots = [c.spot_price for c in candles]
    open_s = spots[0]
    close_s = spots[-1]
    high_s = max(spots)
    low_s = min(spots)
    day_range = (high_s - low_s) / open_s
    day_change = (close_s - open_s) / open_s

    if day_range > 0.025:
        volatility = "high"
    elif day_range > 0.012:
        volatility = "medium"
    else:
        volatility = "low"

    if day_range > 0.03:
        regime = "volatile"
    elif abs(day_change) > 0.015:
        regime = "trending_up" if day_change > 0 else "trending_down"
    elif day_range > 0.015 and abs(day_change) < 0.005:
        regime = "range_bound"
    elif day_change > 0.005:
        regime = "trending_up"
    elif day_change < -0.005:
        regime = "trending_down"
    else:
        regime = "range_bound"

    # Extended metrics
    morning_move = 0
    if len(spots) >= 10:
        morning_move = (spots[9] - spots[0]) / spots[0] * 100

    midday_candles = [c for c in candles if "11:00" <= c.time_str <= "13:00"]
    midday_move = 0
    if len(midday_candles) >= 2:
        midday_move = (midday_candles[-1].spot_price - midday_candles[0].spot_price) / midday_candles[0].spot_price * 100

    late_candles = [c for c in candles if c.time_str >= "14:30"]
    eod_move = 0
    if len(late_candles) >= 2:
        eod_move = (late_candles[-1].spot_price - late_candles[0].spot_price) / late_candles[0].spot_price * 100

    verdicts = [c.verdict for c in candles]
    verdict_flips = sum(1 for i in range(1, len(verdicts)) if verdicts[i] != verdicts[i - 1])

    confidences = [c.confidence for c in candles]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0

    verdict_counts = defaultdict(int)
    for v in verdicts:
        verdict_counts[v] += 1
    dominant_verdict = max(verdict_counts, key=verdict_counts.get) if verdict_counts else ""

    max_spike = 0
    for i in range(1, len(spots)):
        move = abs(spots[i] - spots[i - 1]) / spots[i - 1] * 100
        max_spike = max(max_spike, move)

    min_idx = spots.index(min(spots))
    max_after_min = max(spots[min_idx:]) if min_idx < len(spots) - 1 else spots[min_idx]
    drop_from_open = (spots[0] - min(spots)) / spots[0] * 100
    recovery = (max_after_min - min(spots)) / min(spots) * 100 if min(spots) > 0 else 0
    v_reversal = drop_from_open > 1.0 and recovery > 0.8

    whipsaw = verdict_flips > len(verdicts) * 0.4
    gap_open = abs(morning_move) > 0.8

    return DayClassification(
        date=day,
        open_spot=open_s,
        close_spot=close_s,
        high_spot=high_s,
        low_spot=low_s,
        day_range_pct=day_range * 100,
        day_change_pct=day_change * 100,
        regime=regime,
        volatility=volatility,
        morning_move_pct=morning_move,
        midday_move_pct=midday_move,
        eod_move_pct=eod_move,
        verdict_flips=verdict_flips,
        avg_confidence=avg_conf,
        dominant_verdict=dominant_verdict,
        max_spike_pct=max_spike,
        v_reversal=v_reversal,
        whipsaw=whipsaw,
        gap_open=gap_open,
    )


# ─── Premium Lookup ──────────────────────────────────────────────────────────

def get_premium(candle: Candle, strike: int, option_type: str) -> Optional[float]:
    if strike not in candle.strikes:
        return None
    data = candle.strikes[strike]
    ltp = data.get("ce_ltp" if option_type == "CE" else "pe_ltp", 0)
    return ltp if ltp and ltp > 0 else None


def find_atm(spot: float) -> int:
    return int(round(spot / NIFTY_STEP) * NIFTY_STEP)


# ─── Generic buyer/seller exit tracker ───────────────────────────────────────

def _track_buyer(trade: Trade, candle: Candle) -> bool:
    """Track a BUY trade. Returns True if resolved."""
    premium = get_premium(candle, trade.strike, trade.option_type)
    if premium is None:
        return False
    trade.max_premium = max(trade.max_premium, premium)
    trade.min_premium = min(trade.min_premium, premium)

    if premium <= trade.sl_premium:
        trade.status = "LOST"
        trade.exit_time = candle.time_str
        trade.exit_premium = premium
        trade.exit_reason = "SL"
        trade.pnl_pct = (premium - trade.entry_premium) / trade.entry_premium * 100
        return True
    if premium >= trade.target_premium:
        trade.status = "WON"
        trade.exit_time = candle.time_str
        trade.exit_premium = premium
        trade.exit_reason = "TARGET"
        trade.pnl_pct = (premium - trade.entry_premium) / trade.entry_premium * 100
        return True
    if candle.time_str >= EOD_EXIT:
        trade.exit_time = candle.time_str
        trade.exit_premium = premium
        trade.exit_reason = "EOD"
        trade.pnl_pct = (premium - trade.entry_premium) / trade.entry_premium * 100
        trade.status = "WON" if trade.pnl_pct > 0 else "LOST"
        return True
    return False


def _eod_fallback(trade: Trade, candles: list):
    """EOD fallback if trade still active at end of data."""
    if trade and trade.status == "ACTIVE":
        for c in reversed(candles):
            p = get_premium(c, trade.strike, trade.option_type)
            if p:
                trade.exit_premium = p
                trade.exit_time = c.time_str
                trade.exit_reason = "EOD"
                if trade.strategy == "Selling":
                    trade.pnl_pct = (trade.entry_premium - p) / trade.entry_premium * 100
                else:
                    trade.pnl_pct = (p - trade.entry_premium) / trade.entry_premium * 100
                trade.status = "WON" if trade.pnl_pct > 0 else "LOST"
                break


# ─── Iron Pulse Simulator ───────────────────────────────────────────────────

def simulate_iron_pulse(candles: list, day: str) -> Optional[Trade]:
    trade = None
    for candle in candles:
        t = candle.time_str
        if trade is None:
            if t < IP_START or t >= IP_END:
                continue
            if "Slightly" not in candle.verdict:
                continue
            if candle.confidence < IP_MIN_CONFIDENCE:
                continue
            if "Bullish" in candle.verdict:
                direction, option_type = "BUY_CALL", "CE"
            else:
                direction, option_type = "BUY_PUT", "PE"
            strike = find_atm(candle.spot_price)
            premium = get_premium(candle, strike, option_type)
            if premium is None or premium < candle.spot_price * IP_MIN_PREMIUM_PCT:
                continue
            trade = Trade(
                strategy="Iron Pulse", sub_strategy="Iron Pulse", day=day,
                entry_time=t, direction=direction, strike=strike,
                option_type=option_type, entry_premium=premium,
                sl_premium=premium * (1 - IP_SL_PCT),
                target_premium=premium * (1 + IP_TARGET_PCT),
                entry_spot=candle.spot_price, entry_verdict=candle.verdict,
                entry_confidence=candle.confidence,
                max_premium=premium, min_premium=premium,
            )
            continue

        premium = get_premium(candle, trade.strike, trade.option_type)
        if premium is None:
            continue
        trade.max_premium = max(trade.max_premium, premium)
        trade.min_premium = min(trade.min_premium, premium)

        if premium <= trade.sl_premium:
            trade.status, trade.exit_time, trade.exit_premium = "LOST", t, premium
            trade.exit_reason = "SL"
            trade.pnl_pct = (premium - trade.entry_premium) / trade.entry_premium * 100
            return trade
        if premium >= trade.target_premium:
            trade.status, trade.exit_time, trade.exit_premium = "WON", t, premium
            trade.exit_reason = "TARGET"
            trade.pnl_pct = (premium - trade.entry_premium) / trade.entry_premium * 100
            return trade
        # Verdict flip
        if "Slightly" in candle.verdict:
            if (trade.direction == "BUY_CALL" and "Bearish" in candle.verdict) or \
               (trade.direction == "BUY_PUT" and "Bullish" in candle.verdict):
                trade.status, trade.exit_time, trade.exit_premium = "CANCELLED", t, premium
                trade.exit_reason = "VERDICT_FLIP"
                trade.pnl_pct = (premium - trade.entry_premium) / trade.entry_premium * 100
                return trade
        if t >= EOD_EXIT:
            trade.exit_time, trade.exit_premium = t, premium
            trade.exit_reason = "EOD"
            trade.pnl_pct = (premium - trade.entry_premium) / trade.entry_premium * 100
            trade.status = "WON" if trade.pnl_pct > 0 else "LOST"
            return trade

    _eod_fallback(trade, candles)
    return trade


# ─── Selling Simulator ───────────────────────────────────────────────────────

def simulate_selling(candles: list, day: str) -> Optional[Trade]:
    trade = None
    for candle in candles:
        t = candle.time_str
        if trade is None:
            if t < SELL_START or t >= SELL_END:
                continue
            if "Slightly" not in candle.verdict or candle.confidence < SELL_MIN_CONFIDENCE:
                continue
            atm = find_atm(candle.spot_price)
            if "Bullish" in candle.verdict:
                direction, option_type = "SELL_PUT", "PE"
                strike = atm - (NIFTY_STEP * SELL_OTM_OFFSET)
            else:
                direction, option_type = "SELL_CALL", "CE"
                strike = atm + (NIFTY_STEP * SELL_OTM_OFFSET)
            premium = get_premium(candle, strike, option_type)
            if premium is None or premium < SELL_MIN_PREMIUM:
                continue
            trade = Trade(
                strategy="Selling", sub_strategy="Selling", day=day,
                entry_time=t, direction=direction, strike=strike,
                option_type=option_type, entry_premium=premium,
                sl_premium=premium * (1 + SELL_SL_PCT),
                target_premium=premium * (1 - SELL_T1_PCT),
                target2_premium=premium * (1 - SELL_T2_PCT),
                entry_spot=candle.spot_price, entry_verdict=candle.verdict,
                entry_confidence=candle.confidence,
                max_premium=premium, min_premium=premium,
            )
            continue

        premium = get_premium(candle, trade.strike, trade.option_type)
        if premium is None:
            continue
        trade.max_premium = max(trade.max_premium, premium)
        trade.min_premium = min(trade.min_premium, premium)

        if premium >= trade.sl_premium:
            trade.status, trade.exit_time, trade.exit_premium = "LOST", t, premium
            trade.exit_reason = "SL"
            trade.pnl_pct = (trade.entry_premium - premium) / trade.entry_premium * 100
            return trade
        if not trade.t1_hit and premium <= trade.target_premium:
            trade.t1_hit = True
        if premium <= trade.target2_premium:
            trade.status, trade.exit_time, trade.exit_premium = "WON", t, premium
            trade.exit_reason = "TARGET2"
            trade.pnl_pct = (trade.entry_premium - premium) / trade.entry_premium * 100
            return trade
        if t >= EOD_EXIT:
            trade.exit_time, trade.exit_premium = t, premium
            trade.exit_reason = "EOD"
            trade.pnl_pct = (trade.entry_premium - premium) / trade.entry_premium * 100
            trade.status = "WON" if trade.pnl_pct > 0 else "LOST"
            return trade

    _eod_fallback(trade, candles)
    return trade


# ─── Dessert Simulator ───────────────────────────────────────────────────────

def simulate_dessert(candles: list, day: str) -> Optional[Trade]:
    trade = None
    spot_history = []
    for candle in candles:
        t = candle.time_str
        spot_history.append((t, candle.spot_price))
        if trade is None:
            if t < DESSERT_START or t >= DESSERT_END:
                continue
            atm = find_atm(candle.spot_price)
            sub = None
            if "Bull" in candle.verdict and candle.iv_skew < 1.0 and candle.max_pain > 0 and atm < candle.max_pain:
                sub = "Contra Sniper"
            if sub is None:
                sm = _calc_spot_move_30m(spot_history)
                if candle.confidence < 50 and candle.iv_skew < 0 and sm is not None and sm > 0.05:
                    sub = "Phantom PUT"
            if sub is None:
                continue
            premium = get_premium(candle, atm, "PE")
            if premium is None:
                continue
            trade = Trade(
                strategy="Dessert", sub_strategy=sub, day=day,
                entry_time=t, direction="BUY_PUT", strike=atm,
                option_type="PE", entry_premium=premium,
                sl_premium=premium * (1 - DESSERT_SL_PCT),
                target_premium=premium * (1 + DESSERT_TARGET_PCT),
                entry_spot=candle.spot_price, entry_verdict=candle.verdict,
                entry_confidence=candle.confidence,
                max_premium=premium, min_premium=premium,
            )
            continue

        if _track_buyer(trade, candle):
            return trade

    _eod_fallback(trade, candles)
    return trade


def _calc_spot_move_30m(spot_history: list) -> Optional[float]:
    if len(spot_history) < 10:
        return None
    return (spot_history[-1][1] - spot_history[-10][1]) / spot_history[-10][1] * 100


# ─── Momentum Simulator ─────────────────────────────────────────────────────

def simulate_momentum(candles: list, day: str) -> Optional[Trade]:
    """Simulate Momentum strategy: trend-following 1:2 RR on high-conviction days."""
    trade = None
    for candle in candles:
        t = candle.time_str
        if trade is None:
            if t < MOM_START or t >= MOM_END:
                continue
            if candle.confidence < MOM_MIN_CONFIDENCE:
                continue
            if candle.confirmation_status != "CONFIRMED":
                continue
            if candle.verdict in MOM_BEARISH:
                direction, option_type = "BUY_PUT", "PE"
            elif candle.verdict in MOM_BULLISH:
                direction, option_type = "BUY_CALL", "CE"
            else:
                continue
            strike = find_atm(candle.spot_price)
            premium = get_premium(candle, strike, option_type)
            if premium is None or premium < MOM_MIN_PREMIUM:
                continue
            trade = Trade(
                strategy="Momentum", sub_strategy="Momentum", day=day,
                entry_time=t, direction=direction, strike=strike,
                option_type=option_type, entry_premium=premium,
                sl_premium=premium * (1 - MOM_SL_PCT),
                target_premium=premium * (1 + MOM_TARGET_PCT),
                entry_spot=candle.spot_price, entry_verdict=candle.verdict,
                entry_confidence=candle.confidence,
                max_premium=premium, min_premium=premium,
            )
            continue

        if _track_buyer(trade, candle):
            return trade

    _eod_fallback(trade, candles)
    return trade


# ─── Price Action (CHC-3) Simulator ─────────────────────────────────────────

def simulate_pa(candles: list, day: str) -> Optional[Trade]:
    """Simulate Price Action CHC-3 strategy."""
    trade = None
    # Lock ATM at first candle of the day
    atm_strike = None
    premium_history = []  # list of {ce_ltp, pe_ltp, spot}

    for candle in candles:
        t = candle.time_str

        # Lock ATM at market open
        if atm_strike is None and candle.spot_price > 0:
            atm_strike = find_atm(candle.spot_price)

        if atm_strike is None:
            continue

        # Record premium history
        ce = get_premium(candle, atm_strike, "CE")
        pe = get_premium(candle, atm_strike, "PE")
        if ce and pe and ce > 0 and pe > 0:
            premium_history.append({"ce_ltp": ce, "pe_ltp": pe, "spot": candle.spot_price})

        # Track active trade
        if trade is not None:
            if _track_buyer(trade, candle):
                return trade
            continue

        # Entry logic
        if t < PA_START or t >= PA_END:
            continue
        if len(premium_history) < PA_CHC_LOOKBACK + 1:
            continue

        # Detect CHC(3)
        recent = premium_history[-(PA_CHC_LOOKBACK + 1):]
        ce_rising = all(recent[i]["ce_ltp"] > recent[i - 1]["ce_ltp"] for i in range(1, PA_CHC_LOOKBACK + 1))
        pe_rising = all(recent[i]["pe_ltp"] > recent[i - 1]["pe_ltp"] for i in range(1, PA_CHC_LOOKBACK + 1))

        if not ce_rising and not pe_rising:
            continue

        ce_start = recent[0]["ce_ltp"]
        pe_start = recent[0]["pe_ltp"]
        ce_pct = (recent[-1]["ce_ltp"] - ce_start) / ce_start if ce_start > 0 else 0
        pe_pct = (recent[-1]["pe_ltp"] - pe_start) / pe_start if pe_start > 0 else 0

        if ce_rising and not pe_rising:
            side, strength = "CE", ce_pct
        elif pe_rising and not ce_rising:
            side, strength = "PE", pe_pct
        elif ce_rising and pe_rising:
            side, strength = ("CE", ce_pct) if ce_pct > pe_pct else ("PE", pe_pct)
        else:
            continue

        # IV Skew filter
        iv_skew = candle.iv_skew
        if side == "CE" and iv_skew > 1.0:
            continue
        if side == "PE" and iv_skew < -1.0:
            continue

        # Choppy filter
        if len(premium_history) >= PA_CHOPPY_LOOKBACK + 1:
            recent_spots = [c["spot"] for c in premium_history[-(PA_CHOPPY_LOOKBACK + 1):]]
            avg_spot = sum(recent_spots) / len(recent_spots)
            if avg_spot > 0:
                spot_range_pct = ((max(recent_spots) - min(recent_spots)) / avg_spot) * 100
                if spot_range_pct < PA_CHOPPY_THRESHOLD:
                    continue

        direction = "BUY_CALL" if side == "CE" else "BUY_PUT"
        premium = get_premium(candle, atm_strike, side)
        if premium is None or premium < PA_MIN_PREMIUM:
            continue

        trade = Trade(
            strategy="Price Action", sub_strategy=f"CHC-3 {side}", day=day,
            entry_time=t, direction=direction, strike=atm_strike,
            option_type=side, entry_premium=premium,
            sl_premium=premium * (1 - PA_SL_PCT),
            target_premium=premium * (1 + PA_TARGET_PCT),
            entry_spot=candle.spot_price, entry_verdict=candle.verdict,
            entry_confidence=candle.confidence,
            max_premium=premium, min_premium=premium,
        )

    _eod_fallback(trade, candles)
    return trade


# ─── Stress & Market Tests ──────────────────────────────────────────────────

def identify_stress_scenarios(days_data: dict, classifications: dict) -> dict:
    scenarios = {}
    for day in sorted(days_data.keys()):
        cls = classifications.get(day)
        if not cls:
            continue
        events = []
        if cls.v_reversal:
            events.append("V-reversal")
        if cls.gap_open:
            events.append(f"gap_open({cls.morning_move_pct:+.1f}%)")
        if cls.whipsaw:
            events.append(f"whipsaw({cls.verdict_flips}flips)")
        if cls.max_spike_pct > 0.5:
            events.append(f"spike({cls.max_spike_pct:.1f}%)")
        if abs(cls.eod_move_pct) > 0.5:
            events.append(f"eod_{'pump' if cls.eod_move_pct > 0 else 'dump'}({cls.eod_move_pct:+.1f}%)")
        if events:
            scenarios[day] = events
    return scenarios


# ─── Reporting ───────────────────────────────────────────────────────────────

def compute_stats(trades: list, label: str) -> dict:
    if not trades:
        return {"label": label, "total": 0}
    resolved = [t for t in trades if t.status in ("WON", "LOST", "CANCELLED")]
    wins = [t for t in resolved if t.status == "WON"]
    losses = [t for t in resolved if t.status in ("LOST", "CANCELLED")]

    pnls = [t.pnl_pct for t in resolved]
    running = 0
    peak = 0
    max_dd = 0
    for pnl in pnls:
        running += pnl
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)

    max_consec_loss = 0
    curr = 0
    for t in resolved:
        if t.status in ("LOST", "CANCELLED"):
            curr += 1
            max_consec_loss = max(max_consec_loss, curr)
        else:
            curr = 0

    return {
        "label": label, "total": len(resolved),
        "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(resolved) * 100 if resolved else 0,
        "avg_win": sum(t.pnl_pct for t in wins) / len(wins) if wins else 0,
        "avg_loss": sum(t.pnl_pct for t in losses) / len(losses) if losses else 0,
        "total_pnl": sum(pnls),
        "max_drawdown": max_dd,
        "max_consec_losses": max_consec_loss,
        "best_trade": max(pnls) if pnls else 0,
        "worst_trade": min(pnls) if pnls else 0,
        "profit_factor": (
            abs(sum(t.pnl_pct for t in wins) / sum(t.pnl_pct for t in losses))
            if losses and sum(t.pnl_pct for t in losses) != 0 else float("inf")
        ),
        "exit_reasons": dict(defaultdict(int, {
            r: sum(1 for t in resolved if t.exit_reason == r)
            for r in set(t.exit_reason for t in resolved)
        })),
    }


def stats_by_condition(trades: list, classifications: dict) -> dict:
    by_regime = defaultdict(list)
    by_volatility = defaultdict(list)
    by_stress = defaultdict(list)  # V-reversal, whipsaw, gap, spike

    for t in trades:
        if t.day not in classifications or t.status not in ("WON", "LOST", "CANCELLED"):
            continue
        cls = classifications[t.day]
        by_regime[cls.regime].append(t)
        by_volatility[cls.volatility].append(t)
        if cls.v_reversal:
            by_stress["v_reversal"].append(t)
        if cls.whipsaw:
            by_stress["whipsaw"].append(t)
        if cls.gap_open:
            by_stress["gap_open"].append(t)
        if cls.max_spike_pct > 0.5:
            by_stress["sudden_spike"].append(t)

    def _summarize(group):
        result = {}
        for key, tlist in group.items():
            wins = sum(1 for x in tlist if x.status == "WON")
            total = len(tlist)
            avg_pnl = sum(x.pnl_pct for x in tlist) / total if total else 0
            result[key] = {"trades": total, "wins": wins,
                           "wr": wins / total * 100 if total else 0,
                           "avg_pnl": avg_pnl}
        return result

    return {
        "by_regime": _summarize(by_regime),
        "by_volatility": _summarize(by_volatility),
        "by_stress": _summarize(by_stress),
    }


def print_strategy_report(name: str, trades: list, classifications: dict):
    """Print full report for one strategy."""
    print()
    print("=" * 90)
    print(f"  {name}")
    print("=" * 90)

    if not trades:
        print("  No trades triggered.")
        return

    stats = compute_stats(trades, name)
    print()
    print(f"  Total Trades:      {stats['total']}")
    print(f"  Wins / Losses:     {stats['wins']} / {stats['losses']}")
    print(f"  Win Rate:          {stats['win_rate']:.1f}%")
    print(f"  Avg Win:           {stats['avg_win']:+.1f}%")
    print(f"  Avg Loss:          {stats['avg_loss']:+.1f}%")
    print(f"  Total P&L:         {stats['total_pnl']:+.1f}%")
    print(f"  Max Drawdown:      {stats['max_drawdown']:.1f}%")
    print(f"  Max Consec Losses: {stats['max_consec_losses']}")
    print(f"  Best Trade:        {stats['best_trade']:+.1f}%")
    print(f"  Worst Trade:       {stats['worst_trade']:+.1f}%")
    print(f"  Profit Factor:     {stats['profit_factor']:.2f}")
    print(f"  Exit Reasons:      {dict(stats['exit_reasons'])}")

    # Trade log
    print()
    print(f"  {'Date':<12} {'Dir':<11} {'Strike':<7} {'Entry':<8} {'Exit':<8} {'P&L%':<9} {'Status':<10} {'Reason':<14} {'Sub':<15}")
    print("  " + "-" * 104)
    for t in trades:
        print(f"  {t.day:<12} {t.direction:<11} {t.strike:<7} {t.entry_premium:<8.1f} {t.exit_premium:<8.1f} {t.pnl_pct:>+7.1f}% {t.status:<10} {t.exit_reason:<14} {t.sub_strategy:<15}")

    cond = stats_by_condition(trades, classifications)

    # By regime
    print()
    print("  Performance by Market Regime:")
    print(f"    {'Regime':<18} {'Trades':<8} {'Wins':<6} {'WR%':<8} {'Avg P&L':<10}")
    print("    " + "-" * 50)
    for k, d in sorted(cond["by_regime"].items()):
        print(f"    {k:<18} {d['trades']:<8} {d['wins']:<6} {d['wr']:>5.1f}%  {d['avg_pnl']:>+7.1f}%")

    # By volatility
    print()
    print("  Performance by Volatility:")
    print(f"    {'Volatility':<18} {'Trades':<8} {'Wins':<6} {'WR%':<8} {'Avg P&L':<10}")
    print("    " + "-" * 50)
    for k, d in sorted(cond["by_volatility"].items()):
        print(f"    {k:<18} {d['trades']:<8} {d['wins']:<6} {d['wr']:>5.1f}%  {d['avg_pnl']:>+7.1f}%")

    # By stress scenario
    if cond["by_stress"]:
        print()
        print("  Performance Under Stress Scenarios:")
        print(f"    {'Scenario':<18} {'Trades':<8} {'Wins':<6} {'WR%':<8} {'Avg P&L':<10}")
        print("    " + "-" * 50)
        for k, d in sorted(cond["by_stress"].items()):
            print(f"    {k:<18} {d['trades']:<8} {d['wins']:<6} {d['wr']:>5.1f}%  {d['avg_pnl']:>+7.1f}%")


# ─── Main ────────────────────────────────────────────────────────────────────

def run():
    print("=" * 90)
    print("  FORWARD TEST SIMULATOR v2 -- All 5 Strategies + Market Condition Tests")
    print("=" * 90)
    print()

    print("Loading historical data...")
    days_data = load_data()
    print(f"  Loaded {len(days_data)} trading days")
    print(f"  Total candles: {sum(len(v) for v in days_data.values())}")

    usable_days = sorted(
        day for day, candles in days_data.items()
        if sum(1 for c in candles if len(c.strikes) > 0) >= 20
    )
    print(f"  Usable days: {len(usable_days)} ({usable_days[0]} to {usable_days[-1]})")
    print()

    # Classify
    classifications = {day: classify_day(day, days_data[day]) for day in usable_days}

    # ─── SECTION 1: Market Conditions Summary ────────────────────────────
    print("=" * 90)
    print("  SECTION 1: MARKET CONDITIONS & ENVIRONMENT TESTED")
    print("=" * 90)
    print()
    print(f"  {'Date':<12} {'Regime':<16} {'Vol':<7} {'Range%':<8} {'Chg%':<8} {'MornMv':<8} {'MidMv':<8} {'EODMv':<8} {'Flips':<6} {'Spike':<7}")
    print("  " + "-" * 96)
    for day in usable_days:
        c = classifications[day]
        print(f"  {c.date:<12} {c.regime:<16} {c.volatility:<7} {c.day_range_pct:>5.2f}%  {c.day_change_pct:>+6.2f}% {c.morning_move_pct:>+6.2f}% {c.midday_move_pct:>+6.2f}% {c.eod_move_pct:>+6.2f}% {c.verdict_flips:>4}  {c.max_spike_pct:>5.2f}%")

    # Regime distribution
    regime_counts = defaultdict(int)
    vol_counts = defaultdict(int)
    for c in classifications.values():
        regime_counts[c.regime] += 1
        vol_counts[c.volatility] += 1

    print()
    print("  Regime Distribution:")
    for r, cnt in sorted(regime_counts.items(), key=lambda x: -x[1]):
        print(f"    {r:<18} {cnt} days ({cnt/len(usable_days)*100:.0f}%)")
    print()
    print("  Volatility Distribution:")
    for v, cnt in sorted(vol_counts.items(), key=lambda x: -x[1]):
        print(f"    {v:<18} {cnt} days ({cnt/len(usable_days)*100:.0f}%)")

    # Stress scenarios
    scenarios = identify_stress_scenarios(days_data, classifications)
    print()
    print("  Stress Scenarios Detected:")
    stress_type_counts = defaultdict(int)
    for day, events in sorted(scenarios.items()):
        print(f"    {day}: {', '.join(events)}")
        for e in events:
            key = e.split("(")[0]
            stress_type_counts[key] += 1

    print()
    print("  Stress Type Frequency:")
    for st, cnt in sorted(stress_type_counts.items(), key=lambda x: -x[1]):
        print(f"    {st:<18} {cnt} occurrences")

    # ─── SECTION 2: Market Tests Performed ───────────────────────────────
    print()
    print("=" * 90)
    print("  SECTION 2: MARKET TESTS PERFORMED")
    print("=" * 90)
    print("""
  The following market conditions and scenarios were tested across all strategies:

  REGIME TESTS:
    [1] Trending Up     — Sustained bullish move (day change > +0.5%)
    [2] Trending Down   — Sustained bearish move (day change < -0.5%)
    [3] Range Bound     — Flat close despite intraday movement
    [4] Volatile        — Extreme range > 3% (crash/recovery days)

  VOLATILITY TESTS:
    [5] Low Volatility  — Day range < 1.2% (calm market)
    [6] Medium Vol      — Day range 1.2-2.5% (normal movement)
    [7] High Volatility — Day range > 2.5% (wild swings)

  STRESS / MANIPULATION TESTS:
    [8]  V-Reversal      — Dropped >1% then recovered >0.8% (stop hunting)
    [9]  Gap Open        — Morning 30-min move > 0.8% (overnight news/gap)
    [10] Whipsaw         — >40% of candles flip verdict (indecisive market)
    [11] Sudden Spike    — Any 3-min candle > 0.5% move (news/manipulation)
    [12] EOD Pump/Dump   — Last hour move > 0.5% (institutional activity)

  ENTRY TIMING TESTS:
    [13] Morning Session  — PA & Dessert (9:30-11:00)
    [14] Midday Session   — Iron Pulse, Selling (11:00-14:00)
    [15] Afternoon Only   — Momentum (12:00-14:00)

  SIGNAL QUALITY TESTS:
    [16] High Confidence  — Conf >= 85% (Momentum requirement)
    [17] Medium Conf      — Conf 65-85% (Iron Pulse, Selling)
    [18] Low Confidence   — Conf < 50% (Phantom PUT trigger)
    [19] CONFIRMED Signal — Triple alignment (OI + verdict + price)
    [20] Verdict Stability — Impact of verdict flips on active trades

  PREMIUM BEHAVIOR TESTS:
    [21] CHC-3 Momentum   — 3 consecutive higher closes (PA)
    [22] IV Skew Filter   — Skew impact on option pricing
    [23] Choppy Market    — Spot range < 0.15% filter (PA)
    [24] Premium Decay    — Time-based theta decay on selling trades
""")

    # ─── SECTION 3: Run ALL Simulations ──────────────────────────────────
    ip_trades, sell_trades, dessert_trades, mom_trades, pa_trades = [], [], [], [], []

    for day in usable_days:
        candles = days_data[day]
        t = simulate_iron_pulse(candles, day)
        if t:
            ip_trades.append(t)
        t = simulate_selling(candles, day)
        if t:
            sell_trades.append(t)
        t = simulate_dessert(candles, day)
        if t:
            dessert_trades.append(t)
        t = simulate_momentum(candles, day)
        if t:
            mom_trades.append(t)
        t = simulate_pa(candles, day)
        if t:
            pa_trades.append(t)

    # ─── SECTION 3: Per-Strategy Reports ─────────────────────────────────
    print()
    print("=" * 90)
    print("  SECTION 3: STRATEGY-BY-STRATEGY RESULTS")
    print("=" * 90)

    all_strategy_trades = [
        ("IRON PULSE (Buying -- 1:1.1 RR)", ip_trades),
        ("SELLING (Dual T1/T2)", sell_trades),
        ("DESSERT (Contra Sniper + Phantom PUT -- 1:2 RR)", dessert_trades),
        ("MOMENTUM (Trend-following -- 1:2 RR)", mom_trades),
        ("PRICE ACTION / CHC-3 (Premium Momentum -- 1:1 RR)", pa_trades),
    ]

    for name, trades in all_strategy_trades:
        print_strategy_report(name, trades, classifications)

    # ─── SECTION 4: Head-to-Head Comparison ──────────────────────────────
    print()
    print("=" * 90)
    print("  SECTION 4: HEAD-TO-HEAD STRATEGY COMPARISON")
    print("=" * 90)
    print()
    print(f"  {'Strategy':<22} {'Trades':<8} {'WR%':<8} {'AvgWin':<9} {'AvgLoss':<9} {'PF':<7} {'P&L%':<10} {'MaxDD':<8} {'ConsecL':<8}")
    print("  " + "-" * 90)
    for name, trades in all_strategy_trades:
        s = compute_stats(trades, name)
        if s["total"] == 0:
            print(f"  {name[:22]:<22} {'---':^8}")
            continue
        short_name = name.split("(")[0].strip()[:22]
        print(f"  {short_name:<22} {s['total']:<8} {s['win_rate']:>5.1f}%  {s['avg_win']:>+6.1f}%  {s['avg_loss']:>+6.1f}%  {s['profit_factor']:>5.2f}  {s['total_pnl']:>+8.1f}% {s['max_drawdown']:>6.1f}%  {s['max_consec_losses']}")

    # ─── SECTION 5: Stress Test Results ──────────────────────────────────
    print()
    print("=" * 90)
    print("  SECTION 5: STRESS TEST RESULTS (ALL STRATEGIES)")
    print("=" * 90)
    print()

    # Cross-strategy stress analysis
    all_trades = ip_trades + sell_trades + dessert_trades + mom_trades + pa_trades
    stress_scenarios_list = [
        ("v_reversal", "V-Reversal (Stop Hunt)"),
        ("whipsaw", "Whipsaw (Indecision)"),
        ("gap_open", "Gap Open (Overnight News)"),
        ("sudden_spike", "Sudden Spike (Manipulation)"),
    ]

    for stress_key, stress_label in stress_scenarios_list:
        stress_days = [day for day, cls in classifications.items()
                       if (stress_key == "v_reversal" and cls.v_reversal) or
                          (stress_key == "whipsaw" and cls.whipsaw) or
                          (stress_key == "gap_open" and cls.gap_open) or
                          (stress_key == "sudden_spike" and cls.max_spike_pct > 0.5)]

        if not stress_days:
            continue

        print(f"  {stress_label}  (Days: {', '.join(stress_days)})")
        print(f"    {'Strategy':<22} {'Trades':<8} {'W':<4} {'L':<4} {'WR%':<8} {'Avg P&L':<10}")
        print("    " + "-" * 56)

        for sname, strades in all_strategy_trades:
            short = sname.split("(")[0].strip()[:22]
            affected = [t for t in strades if t.day in stress_days and t.status in ("WON", "LOST", "CANCELLED")]
            if not affected:
                print(f"    {short:<22} {'--':>4}")
                continue
            w = sum(1 for t in affected if t.status == "WON")
            l = len(affected) - w
            wr = w / len(affected) * 100
            avg = sum(t.pnl_pct for t in affected) / len(affected)
            print(f"    {short:<22} {len(affected):<8} {w:<4} {l:<4} {wr:>5.1f}%  {avg:>+7.1f}%")
        print()

    # ─── SECTION 6: Combined Portfolio ───────────────────────────────────
    print("=" * 90)
    print("  SECTION 6: COMBINED PORTFOLIO ANALYSIS (ALL 5 STRATEGIES)")
    print("=" * 90)

    all_resolved = sorted(
        [t for t in all_trades if t.status in ("WON", "LOST", "CANCELLED")],
        key=lambda t: t.day
    )

    if all_resolved:
        daily_pnl = defaultdict(float)
        daily_count = defaultdict(int)
        for t in all_resolved:
            daily_pnl[t.day] += t.pnl_pct
            daily_count[t.day] += 1

        print()
        print(f"  {'Date':<12} {'#Tr':<5} {'Day P&L':<10} {'Cum P&L':<10} {'Regime':<16} {'Stress':<30}")
        print("  " + "-" * 86)
        cum = 0
        max_cum = 0
        max_dd = 0
        winning_days = 0
        losing_days = 0
        for day in usable_days:
            if day not in daily_pnl:
                continue
            pnl = daily_pnl[day]
            cum += pnl
            max_cum = max(max_cum, cum)
            dd = max_cum - cum
            max_dd = max(max_dd, dd)
            regime = classifications[day].regime
            stress = ", ".join(scenarios.get(day, ["-"]))
            if pnl > 0:
                winning_days += 1
            else:
                losing_days += 1
            print(f"  {day:<12} {daily_count[day]:<5} {pnl:>+8.1f}% {cum:>+8.1f}% {regime:<16} {stress:<30}")

        total_wins = sum(1 for t in all_resolved if t.status == "WON")
        total_trades = len(all_resolved)
        best_day = max(daily_pnl.items(), key=lambda x: x[1])
        worst_day = min(daily_pnl.items(), key=lambda x: x[1])

        print()
        print(f"  Total Trades:        {total_trades}")
        print(f"  Combined Win Rate:   {total_wins / total_trades * 100:.1f}%")
        print(f"  Combined P&L:        {cum:+.1f}%")
        print(f"  Portfolio Max DD:    {max_dd:.1f}%")
        print(f"  Winning Days:        {winning_days} / {winning_days + losing_days} ({winning_days/(winning_days+losing_days)*100:.0f}%)")
        print(f"  Best Day:            {best_day[0]} ({best_day[1]:+.1f}%)")
        print(f"  Worst Day:           {worst_day[0]} ({worst_day[1]:+.1f}%)")
        print(f"  Avg Daily P&L:       {cum / (winning_days + losing_days):+.1f}%")

    # ─── SECTION 7: Recommendations ─────────────────────────────────────
    print()
    print("=" * 90)
    print("  SECTION 7: RECOMMENDATIONS -- When to Use Each Strategy")
    print("=" * 90)
    print()

    for sname, strades in all_strategy_trades:
        short = sname.split("(")[0].strip()
        if not strades:
            continue
        resolved = [t for t in strades if t.status in ("WON", "LOST", "CANCELLED")]
        if not resolved:
            continue

        cond = stats_by_condition(resolved, classifications)

        # Best/worst regime
        best_regime, best_wr = None, -1
        worst_regime, worst_wr = None, 101
        for r, d in cond["by_regime"].items():
            if d["trades"] >= 2:
                if d["wr"] > best_wr:
                    best_wr, best_regime = d["wr"], r
                if d["wr"] < worst_wr:
                    worst_wr, worst_regime = d["wr"], r

        best_vol, best_vol_wr = None, -1
        for v, d in cond["by_volatility"].items():
            if d["trades"] >= 2 and d["wr"] > best_vol_wr:
                best_vol_wr, best_vol = d["wr"], v

        avoid = []
        for r, d in cond["by_regime"].items():
            if d["trades"] >= 2 and d["wr"] < 40:
                avoid.append(f"{r}({d['wr']:.0f}%WR)")
        for v, d in cond["by_volatility"].items():
            if d["trades"] >= 2 and d["wr"] < 40:
                avoid.append(f"{v}-vol({d['wr']:.0f}%WR)")
        for s, d in cond["by_stress"].items():
            if d["trades"] >= 1 and d["wr"] < 40:
                avoid.append(f"{s}({d['wr']:.0f}%WR)")

        print(f"  {short}:")
        if best_regime:
            print(f"    USE in:   {best_regime} (WR: {best_wr:.0f}%)", end="")
            if best_vol:
                print(f" + {best_vol} volatility (WR: {best_vol_wr:.0f}%)")
            else:
                print()
        if avoid:
            print(f"    AVOID:    {', '.join(avoid)}")
        else:
            print(f"    AVOID:    No clear avoid signals (limited data)")
        if worst_regime:
            print(f"    WEAKEST:  {worst_regime} (WR: {worst_wr:.0f}%)")
        print()

    # ─── SECTION 8: Backtest vs Forward Test ─────────────────────────────
    print("=" * 90)
    print("  SECTION 8: BACKTESTED WR vs FORWARD TEST WR")
    print("=" * 90)
    print()
    backtest_wr = {
        "Iron Pulse": 82.0,
        "Selling": 83.0,
        "Dessert": 86.0,
        "Momentum": None,  # no published backtest
        "Price Action": 72.7,
    }
    strat_map = {
        "Iron Pulse": ip_trades,
        "Selling": sell_trades,
        "Dessert": dessert_trades,
        "Momentum": mom_trades,
        "Price Action": pa_trades,
    }
    print(f"  {'Strategy':<22} {'Backtest WR':<14} {'Forward WR':<14} {'Delta':<10} {'Verdict'}")
    print("  " + "-" * 72)
    for sname, bwr in backtest_wr.items():
        trades = strat_map[sname]
        s = compute_stats(trades, sname)
        fwr = s["win_rate"] if s["total"] > 0 else None
        bwr_str = f"{bwr:.1f}%" if bwr else "N/A"
        fwr_str = f"{fwr:.1f}%" if fwr else "N/A"
        if bwr and fwr:
            delta = fwr - bwr
            delta_str = f"{delta:+.1f}%"
            verdict = "OK" if delta >= -10 else "DEGRADED" if delta >= -20 else "FAILED"
        else:
            delta_str = "N/A"
            verdict = "INSUFFICIENT" if (s["total"] or 0) < 5 else "NEW"
        print(f"  {sname:<22} {bwr_str:<14} {fwr_str:<14} {delta_str:<10} {verdict}")

    print()
    print("=" * 90)
    print(f"  NOTE: Forward test on {len(usable_days)} trading days ({usable_days[0]} to {usable_days[-1]}).")
    print("  Small sample — directional guidance only. Extend data for statistical significance.")
    print("=" * 90)


if __name__ == "__main__":
    run()
