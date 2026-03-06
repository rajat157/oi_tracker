"""
PA V3: Swing Reversal + CHC/CLC on 1-Min Nifty
================================================

Strategy:
  1. Detect swing High/Low on NIFTY 1-min chart
  2. After swing LOW  -> CHC-3 (3 Consecutive Higher Closes on spot) -> BUY CE ATM
  3. After swing HIGH -> CLC-3 (3 Consecutive Lower Closes on spot)  -> BUY PE ATM
  4. SL = previous swing low on ATM option 3-min chart (wick low, not close)
  5. Target = 1:1 RR
  6. Skip if ATM option has poor price action on 3-min chart

Data:
  - Kite Historical API: NIFTY 50 1-min + ATM option 3-min OHLC
  - Database oi_snapshots fallback for option LTP

Usage:
  uv run python scripts/pa_v3_swing_test.py [--days 30] [--debug]
"""

import os
import sys
import time
import argparse
from datetime import datetime, date, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from collections import defaultdict
import sqlite3
import json

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from kiteconnect import KiteConnect

# ─── Constants ────────────────────────────────────────────────────────────────

NIFTY_TOKEN = 256265        # NSE:NIFTY 50 instrument token
NIFTY_STEP = 50
DB_PATH = ROOT / "oi_tracker.db"

# Strategy parameters
SWING_N = 2                 # Candles each side to confirm swing (uses CLOSE prices)
ATR_PERIOD = 14             # ATR lookback for dynamic swing depth threshold
MIN_SWING_DEPTH_ATR = 1.5   # Swing must be >= 1.5x ATR deep (filters noise)
MIN_SWING_DEPTH_PTS = 15    # Absolute floor: swing must be >= 15 NIFTY points deep
MIN_SWING_GAP = 8           # Minimum candles between same-type swings
PA_START = "09:30"
PA_END = "14:00"
EOD_EXIT = "15:20"
MIN_PREMIUM = 5.0
MIN_RISK_PCT = 0.02         # Minimum 2% risk (entry - SL) / entry
GOOD_PA_MIN_RANGE_PCT = 1.5 # Min range% over lookback for "good PA"
GOOD_PA_LOOKBACK = 5        # 3-min candles to evaluate PA quality

DEBUG = False


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Candle:
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    @property
    def time_str(self) -> str:
        return self.dt.strftime("%H:%M")


@dataclass
class Trade:
    day: str
    entry_time: str
    direction: str          # "BUY_CE" or "BUY_PE"
    strike: int
    option_type: str        # "CE" or "PE"
    entry_premium: float
    sl_premium: float
    target_premium: float
    risk: float
    entry_spot: float
    swing_type: str
    swing_price: float
    status: str = "ACTIVE"
    exit_time: str = ""
    exit_premium: float = 0
    exit_reason: str = ""
    pnl_pct: float = 0
    max_premium: float = 0
    min_premium: float = 0
    pa_quality: str = ""
    trade_num: int = 0
    signal: str = ""        # "CHC" (higher closes) or "CLC" (lower closes)
    swing_depth: float = 0  # NIFTY points depth of the swing
    atr: float = 0          # ATR at signal time
    data_source: str = ""   # "kite" or "db"
    oi_filter: str = ""      # "PASS", "REJECT", "SKIP" (no data), "" (CLC/unfiltered)
    oi_context: str = ""     # Compact summary of OI state


@dataclass
class OIContext:
    available: bool = False
    futures_basis: float = 0.0
    combined_score: float = 0.0
    signal_confidence: float = 0.0
    verdict: str = ""
    pm_patterns_30min: int = 0
    filter_passed: bool = True
    filter_reason: str = ""
    capitulation: bool = False


# ─── Kite Connection ─────────────────────────────────────────────────────────

def connect_kite() -> KiteConnect:
    api_key = os.environ.get("KITE_API_KEY", "")
    if not api_key:
        print("ERROR: KITE_API_KEY not in .env")
        sys.exit(1)

    kite = KiteConnect(api_key=api_key)

    # Try database token first
    try:
        from kite_auth import load_token
        token = load_token()
        if token:
            kite.set_access_token(token)
            # Verify with a simple call
            kite.ltp("NSE:NIFTY 50")
            print(f"  Kite connected (DB token: {token[:8]}...)")
            return kite
    except Exception as e:
        if DEBUG:
            print(f"  DB token failed: {e}")

    # Fallback to .env token
    token = os.environ.get("KITE_ACCESS_TOKEN", "")
    if token:
        kite.set_access_token(token)
        try:
            kite.ltp("NSE:NIFTY 50")
            print(f"  Kite connected (.env token: {token[:8]}...)")
            return kite
        except Exception as e:
            print(f"  .env token failed: {e}")

    print("ERROR: No valid Kite token. Run kite_auth.py first.")
    sys.exit(1)


# ─── Data Fetching ────────────────────────────────────────────────────────────

def strip_tz(dt):
    """Strip timezone info for consistent comparisons."""
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def fetch_nifty_1min(kite: KiteConnect, from_date: date, to_date: date) -> List[Candle]:
    """Fetch NIFTY 50 1-minute candles from Kite."""
    all_candles = []
    current = from_date

    while current <= to_date:
        chunk_end = min(current + timedelta(days=55), to_date)
        print(f"  Fetching NIFTY 1-min: {current} to {chunk_end}...")
        try:
            data = kite.historical_data(
                instrument_token=NIFTY_TOKEN,
                from_date=current,
                to_date=chunk_end,
                interval="minute"
            )
            for d in data:
                all_candles.append(Candle(
                    dt=strip_tz(d["date"]),
                    open=d["open"], high=d["high"],
                    low=d["low"], close=d["close"],
                    volume=d.get("volume", 0)
                ))
        except Exception as e:
            print(f"  ERROR: {e}")

        current = chunk_end + timedelta(days=1)
        time.sleep(0.3)

    print(f"  Total NIFTY 1-min candles: {len(all_candles)}")
    return all_candles


def get_option_tokens(kite: KiteConnect) -> Dict[Tuple[int, str, str], int]:
    """Download NFO instruments and build (strike, type, expiry) -> token map."""
    from kite_instruments import InstrumentMap

    api_key = os.environ.get("KITE_API_KEY", "")
    imap = InstrumentMap(api_key=api_key)
    if hasattr(kite, '_access_token'):
        imap.set_access_token(kite._access_token)
    elif hasattr(kite, 'access_token'):
        imap.set_access_token(kite.access_token)

    if not imap.refresh():
        print("  WARNING: Could not load instruments")
        return {}

    token_map = {}
    for (strike, otype, expiry), inst in imap._options.items():
        token_map[(int(strike), otype, expiry)] = inst["instrument_token"]

    expiries = sorted(set(e for _, _, e in token_map))
    print(f"  Loaded {len(token_map)} option instruments, expiries: {expiries[:6]}")
    return token_map


def fetch_option_3min(kite: KiteConnect, token: int,
                      from_date: date, to_date: date) -> List[Candle]:
    """Fetch option 3-minute OHLC candles from Kite."""
    try:
        data = kite.historical_data(
            instrument_token=token,
            from_date=from_date,
            to_date=to_date,
            interval="3minute"
        )
        return [Candle(
            dt=strip_tz(d["date"]),
            open=d["open"], high=d["high"],
            low=d["low"], close=d["close"],
            volume=d.get("volume", 0)
        ) for d in data]
    except Exception as e:
        if DEBUG:
            print(f"    Kite option fetch failed (token {token}): {e}")
        return []


def get_weekly_expiry(d: date) -> str:
    """Get the weekly expiry (Tuesday) for a given date as YYYY-MM-DD."""
    days_until_tuesday = (1 - d.weekday()) % 7
    if days_until_tuesday == 0:
        exp = d  # It's Tuesday
    else:
        exp = d + timedelta(days=days_until_tuesday)
    return exp.strftime("%Y-%m-%d")


def find_atm(spot: float) -> int:
    return int(round(spot / NIFTY_STEP) * NIFTY_STEP)


# ─── Database Fallback ────────────────────────────────────────────────────────

def load_db_option_candles(day: str, strike: int, opt_type: str) -> List[Candle]:
    """
    Load option LTP from database oi_snapshots and build pseudo 3-min candles.
    Each snapshot becomes a candle with open=close=ltp, high=low=ltp.
    """
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    col = "ce_ltp" if opt_type == "CE" else "pe_ltp"
    cur.execute(f"""
        SELECT timestamp, {col}
        FROM oi_snapshots
        WHERE timestamp LIKE ? AND strike_price = ? AND {col} > 0
        ORDER BY timestamp
    """, (f"{day}%", strike))
    rows = cur.fetchall()
    conn.close()

    candles = []
    for ts_str, ltp in rows:
        try:
            clean = ts_str[:19].replace("T", " ")
            dt = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                clean = ts_str[:16].replace("T", " ")
                dt = datetime.strptime(clean, "%Y-%m-%d %H:%M")
            except ValueError:
                continue
        # Use raw LTP for all fields; swing detection works on close values
        candles.append(Candle(dt=dt, open=ltp, high=ltp, low=ltp, close=ltp))

    return candles


# ─── OI Data Loading ─────────────────────────────────────────────────────────

def load_analysis_history_for_day(day: str) -> list:
    """Load all analysis_history rows for a given day from the database."""
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT timestamp, futures_basis, signal_confidence, verdict, analysis_json
            FROM analysis_history WHERE timestamp LIKE ? ORDER BY timestamp
        """, (f"{day}%",))
        rows = cur.fetchall()
    except Exception:
        conn.close()
        return []
    conn.close()

    result = []
    for ts_str, futures_basis, signal_confidence, verdict, analysis_json in rows:
        try:
            dt = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue

        combined_score = 0.0
        if analysis_json:
            try:
                aj = json.loads(analysis_json)
                combined_score = aj.get("combined_score", 0.0)
            except (json.JSONDecodeError, TypeError):
                pass

        result.append({
            "dt": dt,
            "futures_basis": futures_basis or 0.0,
            "signal_confidence": signal_confidence or 0.0,
            "verdict": verdict or "",
            "combined_score": combined_score,
        })

    return result


def load_pm_patterns_for_day(day: str) -> list:
    """Load PM patterns (detected_patterns) for a given day from the database."""
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT detected_at, pattern_type FROM detected_patterns
            WHERE detected_at LIKE ? ORDER BY detected_at
        """, (f"{day}%",))
        rows = cur.fetchall()
    except Exception:
        conn.close()
        return []
    conn.close()

    result = []
    for ts_str, pattern_type in rows:
        try:
            dt = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue
        result.append({"dt": dt, "pattern_type": pattern_type or ""})

    return result


# ─── ATR Calculation ──────────────────────────────────────────────────────────

def compute_atr(candles: List[Candle], period: int = ATR_PERIOD) -> List[float]:
    """
    Compute ATR(period) for each candle. Returns list same length as candles.
    Uses true range: max(high-low, |high-prev_close|, |low-prev_close|).
    First `period` values use expanding window.
    """
    if not candles:
        return []

    atrs = [0.0] * len(candles)
    tr_sum = 0.0

    for i in range(len(candles)):
        if i == 0:
            tr = candles[i].high - candles[i].low
        else:
            prev_close = candles[i - 1].close
            tr = max(
                candles[i].high - candles[i].low,
                abs(candles[i].high - prev_close),
                abs(candles[i].low - prev_close)
            )

        if i < period:
            tr_sum += tr
            atrs[i] = tr_sum / (i + 1)  # expanding average
        else:
            # EMA-style ATR
            atrs[i] = (atrs[i - 1] * (period - 1) + tr) / period

    return atrs


# ─── Pattern Detection ────────────────────────────────────────────────────────
#
# Atomic 5-candle pattern (all using CLOSE prices) + ATR depth filter:
#
#   Swing LOW + CHC (BUY CE):
#     candle[i-4] candle[i-3]  candle[i-2]  candle[i-1]  candle[i]
#                              ^^SWING LOW^^  ^^CHC-1^^    ^^CHC-2^^
#     - candle[i-2].close < candle[i-3].close AND < candle[i-4].close
#     - candle[i-1].close > candle[i-2].close  (1st higher close)
#     - candle[i].close   > candle[i-1].close  (2nd higher close -> ENTER)
#     - swing depth (highest preceding close - swing close) >= 1.5 * ATR
#
#   Swing HIGH + CLC (BUY PE): mirror image
#

def check_swing_low_chc(candles: List[Candle], i: int, atrs: List[float],
                         last_swing_low_idx: int) -> Tuple[bool, float]:
    """
    Atomic check at candle i: is candle[i-2] a swing low (by CLOSE)
    with candles [i-1] and [i] forming consecutive higher closes?
    Also checks ATR-based minimum depth and gap from last swing.

    Returns (is_valid, swing_depth_pts).
    """
    if i < 4:
        return False, 0

    swing = candles[i - 2].close

    # Structure check: swing low with 2 higher closes after
    if not (swing < candles[i - 3].close and
            swing < candles[i - 4].close and
            candles[i - 1].close > swing and
            candles[i].close > candles[i - 1].close):
        return False, 0

    # Minimum gap from last same-type swing
    if last_swing_low_idx >= 0 and (i - 2) - last_swing_low_idx < MIN_SWING_GAP:
        return False, 0

    # Depth check: how far did price drop to form this swing?
    # Look back up to 15 candles before the swing to find the preceding high
    lookback_start = max(0, i - 2 - 15)
    preceding_high = max(c.close for c in candles[lookback_start:i - 2])
    depth = preceding_high - swing

    # Must meet both ATR-based and absolute minimum depth
    atr = atrs[i] if atrs[i] > 0 else 10  # fallback
    min_depth = max(MIN_SWING_DEPTH_ATR * atr, MIN_SWING_DEPTH_PTS)

    if depth < min_depth:
        return False, depth

    return True, depth


def check_swing_high_clc(candles: List[Candle], i: int, atrs: List[float],
                          last_swing_high_idx: int) -> Tuple[bool, float]:
    """
    Atomic check at candle i: is candle[i-2] a swing high (by CLOSE)
    with candles [i-1] and [i] forming consecutive lower closes?
    Also checks ATR-based minimum depth and gap from last swing.

    Returns (is_valid, swing_depth_pts).
    """
    if i < 4:
        return False, 0

    swing = candles[i - 2].close

    # Structure check: swing high with 2 lower closes after
    if not (swing > candles[i - 3].close and
            swing > candles[i - 4].close and
            candles[i - 1].close < swing and
            candles[i].close < candles[i - 1].close):
        return False, 0

    # Minimum gap from last same-type swing
    if last_swing_high_idx >= 0 and (i - 2) - last_swing_high_idx < MIN_SWING_GAP:
        return False, 0

    # Depth check: how far did price rise to form this swing?
    lookback_start = max(0, i - 2 - 15)
    preceding_low = min(c.close for c in candles[lookback_start:i - 2])
    depth = swing - preceding_low

    atr = atrs[i] if atrs[i] > 0 else 10
    min_depth = max(MIN_SWING_DEPTH_ATR * atr, MIN_SWING_DEPTH_PTS)

    if depth < min_depth:
        return False, depth

    return True, depth


def detect_option_swing_lows(candles_3m: List[Candle], n: int = 2) -> List[Candle]:
    """Detect swing lows on 3-min option chart (wick low). Returns candles at swing low points."""
    lows = []
    for i in range(n, len(candles_3m) - n):
        neighbors = list(range(i - n, i + n + 1))
        neighbors.remove(i)
        if all(candles_3m[i].low < candles_3m[j].low for j in neighbors):
            lows.append(candles_3m[i])
    return lows


# ─── Filters ─────────────────────────────────────────────────────────────────

def is_good_pa(candles_3m: List[Candle], up_to_idx: int) -> Tuple[bool, str]:
    """Check if option has had good price action on 3-min chart."""
    start = max(0, up_to_idx - GOOD_PA_LOOKBACK)
    window = candles_3m[start:up_to_idx + 1]

    if len(window) < 3:
        return False, "insufficient_data"

    avg_price = sum(c.close for c in window) / len(window)
    if avg_price <= 0:
        return False, "zero_price"

    max_high = max(c.high for c in window)
    min_low = min(c.low for c in window)
    range_pct = (max_high - min_low) / avg_price * 100

    bodies = [abs(c.close - c.open) for c in window]
    avg_body_pct = (sum(bodies) / len(bodies)) / avg_price * 100

    is_good = range_pct >= GOOD_PA_MIN_RANGE_PCT
    desc = f"range={range_pct:.1f}% body={avg_body_pct:.1f}%"
    return is_good, desc


# ─── OI Filter ───────────────────────────────────────────────────────────────

def evaluate_oi_filter(signal_time: datetime, signal_type: str,
                       analysis_rows: list, pm_patterns: list,
                       strict: bool = False) -> OIContext:
    """
    Evaluate OI-based filter for a trade signal.
    CLC signals bypass the filter. CHC signals are checked against OI conditions.
    """
    # CLC signals always pass through unfiltered
    if signal_type == "CLC":
        return OIContext(available=True, filter_passed=True, filter_reason="CLC_BYPASS")

    # Find nearest analysis_history row within 5 minutes of signal_time
    if not analysis_rows:
        return OIContext(available=False, filter_passed=True, filter_reason="NO_DATA")

    best_row = None
    best_gap = timedelta(minutes=6)  # > 5 min threshold
    for row in analysis_rows:
        gap = abs(signal_time - row["dt"])
        if gap < best_gap:
            best_gap = gap
            best_row = row

    if best_row is None or best_gap > timedelta(minutes=5):
        return OIContext(available=False, filter_passed=True, filter_reason="NO_DATA")

    futures_basis = best_row["futures_basis"]
    combined_score = best_row["combined_score"]
    signal_confidence = best_row["signal_confidence"]
    verdict = best_row["verdict"]

    # Check capitulation: any row in last 15 min with combined_score < -40
    cap_cutoff = signal_time - timedelta(minutes=15)
    capitulation = any(
        r["combined_score"] < -40
        for r in analysis_rows
        if cap_cutoff <= r["dt"] <= signal_time
    )

    # Count PM patterns in last 30 min
    pm_cutoff = signal_time - timedelta(minutes=30)
    pm_count = sum(
        1 for p in pm_patterns
        if pm_cutoff <= p["dt"] <= signal_time
    )

    # Apply filter rules
    # Standard: futures_basis > 30 AND (combined_score > -10 OR capitulation)
    if strict:
        passed = (futures_basis > 30
                  and (combined_score > -10 or capitulation)
                  and pm_count >= 1
                  and signal_confidence < 75)
    else:
        passed = (futures_basis > 30
                  and (combined_score > -10 or capitulation))

    # Build compact reason string
    cap_str = "Y" if capitulation else "N"
    result_str = "PASS" if passed else "REJECT"
    reason = (f"basis={futures_basis:+.0f} score={combined_score:.0f} "
              f"cap={cap_str} pm={pm_count} conf={signal_confidence:.0f} -> {result_str}")

    return OIContext(
        available=True,
        futures_basis=futures_basis,
        combined_score=combined_score,
        signal_confidence=signal_confidence,
        verdict=verdict,
        pm_patterns_30min=pm_count,
        filter_passed=passed,
        filter_reason=reason,
        capitulation=capitulation,
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def find_opt_candle_idx(candles_3m: List[Candle], target_dt: datetime) -> Optional[int]:
    """Find index of 3-min candle at or just before target_dt."""
    best = None
    for i, c in enumerate(candles_3m):
        if c.dt <= target_dt:
            best = i
        else:
            break
    return best


def group_candles_by_day(candles: List[Candle]) -> Dict[str, List[Candle]]:
    days = defaultdict(list)
    for c in candles:
        days[c.dt.strftime("%Y-%m-%d")].append(c)
    return dict(days)


# ─── Trade Simulation ────────────────────────────────────────────────────────

def simulate_day(day_1m: List[Candle],
                 opt_data: Dict[Tuple[int, str], List[Candle]],
                 day: str,
                 max_trades: int = 1,
                 pa_start: str = "09:30",
                 analysis_rows=None,
                 pm_patterns=None,
                 oi_filter_mode: str = "standard") -> List[Trade]:
    """
    Simulate the swing + CHC-3 strategy for one day.

    opt_data: {(strike, "CE"/"PE"): [3-min candles]} for this day
    """
    trades = []
    active_trade: Optional[Trade] = None
    trade_count = 0

    # Compute ATR for the day's 1-min candles
    atrs = compute_atr(day_1m, ATR_PERIOD)

    # Track last swing indices for gap enforcement
    last_swing_low_idx = -999
    last_swing_high_idx = -999

    for i, candle in enumerate(day_1m):
        t = candle.time_str

        # ─── Track active trade ────────────────────────────────────
        if active_trade is not None:
            key = (active_trade.strike, active_trade.option_type)
            opt_candles = opt_data.get(key, [])
            oidx = find_opt_candle_idx(opt_candles, candle.dt)
            if oidx is None:
                continue

            oc = opt_candles[oidx]
            active_trade.max_premium = max(active_trade.max_premium, oc.high)
            active_trade.min_premium = min(active_trade.min_premium, oc.low)

            # SL hit (check candle low)
            if oc.low <= active_trade.sl_premium:
                active_trade.status = "LOST"
                active_trade.exit_time = t
                active_trade.exit_premium = active_trade.sl_premium
                active_trade.exit_reason = "SL"
                active_trade.pnl_pct = (active_trade.sl_premium - active_trade.entry_premium) / active_trade.entry_premium * 100
                trades.append(active_trade)
                active_trade = None
                if trade_count >= max_trades:
                    break
                continue

            # Target hit (check candle high)
            if oc.high >= active_trade.target_premium:
                active_trade.status = "WON"
                active_trade.exit_time = t
                active_trade.exit_premium = active_trade.target_premium
                active_trade.exit_reason = "TARGET"
                active_trade.pnl_pct = (active_trade.target_premium - active_trade.entry_premium) / active_trade.entry_premium * 100
                trades.append(active_trade)
                active_trade = None
                if trade_count >= max_trades:
                    break
                continue

            # EOD exit
            if t >= EOD_EXIT:
                p = oc.close
                active_trade.exit_time = t
                active_trade.exit_premium = p
                active_trade.exit_reason = "EOD"
                active_trade.pnl_pct = (p - active_trade.entry_premium) / active_trade.entry_premium * 100
                active_trade.status = "WON" if active_trade.pnl_pct > 0 else "LOST"
                trades.append(active_trade)
                active_trade = None
                break
            continue

        # ─── Entry logic (atomic 5-candle pattern) ──────────────────
        if t < pa_start or t >= PA_END:
            continue
        if trade_count >= max_trades:
            continue

        # Check for swing LOW + CHC (BUY CE) or swing HIGH + CLC (BUY PE)
        is_chc, chc_depth = check_swing_low_chc(day_1m, i, atrs, last_swing_low_idx)
        is_clc, clc_depth = check_swing_high_clc(day_1m, i, atrs, last_swing_high_idx)

        if is_chc:
            direction = "BUY_CE"
            opt_type = "CE"
            swing_price = day_1m[i - 2].close
            signal_type = "CHC"
            swing_depth = chc_depth
            last_swing_low_idx = i - 2
        elif is_clc:
            direction = "BUY_PE"
            opt_type = "PE"
            swing_price = day_1m[i - 2].close
            signal_type = "CLC"
            swing_depth = clc_depth
            last_swing_high_idx = i - 2
        else:
            continue

        # OI Filter (CHC only)
        if analysis_rows is not None and oi_filter_mode != "disabled":
            oi_ctx = evaluate_oi_filter(candle.dt, signal_type, analysis_rows, pm_patterns or [], strict=(oi_filter_mode == "strict"))
            if not oi_ctx.filter_passed:
                if DEBUG:
                    print(f"    [{t}] OI REJECT {signal_type}: {oi_ctx.filter_reason}")
                continue
        else:
            oi_ctx = None

        atm = find_atm(candle.close)
        key = (atm, opt_type)
        opt_candles = opt_data.get(key, [])

        if not opt_candles:
            if DEBUG:
                print(f"    [{t}] No option data for {atm} {opt_type}")
            continue

        oidx = find_opt_candle_idx(opt_candles, candle.dt)
        if oidx is None or oidx < 2:
            continue

        entry_premium = opt_candles[oidx].close
        if entry_premium < MIN_PREMIUM:
            continue

        # Good PA filter
        good, pa_desc = is_good_pa(opt_candles, oidx)
        if not good:
            if DEBUG:
                print(f"    [{t}] Poor PA for {atm} {opt_type}: {pa_desc}")
            continue

        # SL from previous swing low on 3-min option chart (wick low)
        # Use n=1 for DB data (sparser), n=2 for Kite OHLC data
        opt_sl_n = 1 if opt_candles[0].volume == 0 else 2
        opt_swing_lows = detect_option_swing_lows(opt_candles[:oidx + 1], n=opt_sl_n)
        if not opt_swing_lows:
            if DEBUG:
                print(f"    [{t}] No option swing low found for SL")
            continue

        sl_raw = opt_swing_lows[-1].low  # Wick low of most recent swing low
        # For DB data (no real wicks), add 1% buffer below to approximate wick
        sl_premium = sl_raw * 0.99 if opt_candles[0].volume == 0 else sl_raw
        if sl_premium >= entry_premium:
            continue  # SL must be below entry for a buy

        risk = entry_premium - sl_premium
        risk_pct = risk / entry_premium
        if risk_pct < MIN_RISK_PCT:
            continue  # Risk too small

        target_premium = entry_premium + risk  # 1:1 RR

        trade_count += 1
        data_src = "kite" if opt_candles[0].volume > 0 else "db"
        # Determine OI filter label
        if oi_ctx is not None:
            if not oi_ctx.available:
                oi_label = "SKIP"
            elif oi_ctx.filter_passed:
                oi_label = "PASS" if signal_type == "CHC" else ""
            else:
                oi_label = "REJECT"  # shouldn't reach here (already continued)
            oi_summary = oi_ctx.filter_reason
        else:
            oi_label = ""
            oi_summary = ""

        active_trade = Trade(
            day=day, entry_time=t, direction=direction,
            strike=atm, option_type=opt_type,
            entry_premium=entry_premium, sl_premium=sl_premium,
            target_premium=target_premium, risk=risk,
            entry_spot=candle.close,
            swing_type="LOW" if signal_type == "CHC" else "HIGH",
            swing_price=swing_price,
            max_premium=entry_premium, min_premium=entry_premium,
            pa_quality=pa_desc, trade_num=trade_count,
            signal=signal_type, swing_depth=swing_depth, atr=atrs[i],
            data_source=data_src,
            oi_filter=oi_label, oi_context=oi_summary,
        )

        if DEBUG:
            print(f"    [{t}] ENTRY: {signal_type} -> {direction} {atm}{opt_type} @ {entry_premium:.1f} "
                  f"SL={sl_premium:.1f} T={target_premium:.1f} risk={risk:.1f} ({risk_pct:.1%}) "
                  f"depth={swing_depth:.0f}pts ATR={atrs[i]:.1f}")

        # After entry, the loop continues tracking the active trade

    # EOD fallback for still-active trade
    if active_trade is not None:
        key = (active_trade.strike, active_trade.option_type)
        opt_candles = opt_data.get(key, [])
        if opt_candles:
            last = opt_candles[-1]
            active_trade.exit_time = last.time_str
            active_trade.exit_premium = last.close
            active_trade.exit_reason = "EOD"
            active_trade.pnl_pct = (last.close - active_trade.entry_premium) / active_trade.entry_premium * 100
            active_trade.status = "WON" if active_trade.pnl_pct > 0 else "LOST"
            trades.append(active_trade)

    return trades


# ─── Statistics ───────────────────────────────────────────────────────────────

def compute_stats(trades: List[Trade]) -> Optional[dict]:
    resolved = [t for t in trades if t.status in ("WON", "LOST")]
    if not resolved:
        return None

    wins = [t for t in resolved if t.status == "WON"]
    losses = [t for t in resolved if t.status == "LOST"]
    pnls = [t.pnl_pct for t in resolved]

    running = peak = max_dd = 0
    for p in pnls:
        running += p
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)

    max_cl = cl = 0
    for t in resolved:
        if t.status == "LOST":
            cl += 1
            max_cl = max(max_cl, cl)
        else:
            cl = 0

    loss_sum = sum(t.pnl_pct for t in losses)
    return {
        "total": len(resolved), "wins": len(wins), "losses": len(losses),
        "wr": len(wins) / len(resolved) * 100,
        "avg_win": sum(t.pnl_pct for t in wins) / len(wins) if wins else 0,
        "avg_loss": loss_sum / len(losses) if losses else 0,
        "pnl": sum(pnls), "max_dd": max_dd, "max_cl": max_cl,
        "best": max(pnls), "worst": min(pnls),
        "pf": abs(sum(t.pnl_pct for t in wins) / loss_sum) if loss_sum else float("inf"),
        "exits": dict(defaultdict(int, {
            r: sum(1 for t in resolved if t.exit_reason == r)
            for r in set(t.exit_reason for t in resolved)
        })),
    }


def print_stats(label: str, trades: List[Trade]):
    print()
    print("=" * 95)
    print(f"  {label}")
    print("=" * 95)
    s = compute_stats(trades)
    if not s:
        print("  No trades.")
        return

    print()
    print(f"  Trades:    {s['total']:<6} WR: {s['wr']:.1f}%")
    print(f"  W/L:       {s['wins']}/{s['losses']}")
    print(f"  Avg Win:   {s['avg_win']:+.1f}%   Avg Loss: {s['avg_loss']:+.1f}%")
    print(f"  Total P&L: {s['pnl']:+.1f}%")
    print(f"  Max DD:    {s['max_dd']:.1f}%")
    print(f"  Max CL:    {s['max_cl']}")
    print(f"  Best:      {s['best']:+.1f}%   Worst: {s['worst']:+.1f}%")
    print(f"  PF:        {s['pf']:.2f}")
    print(f"  Exits:     {s['exits']}")
    print()

    hdr = f"  {'Date':<12} {'#':<3} {'Sig':<4} {'Dir':<8} {'Str':<7} {'Entry':<8} {'SL':<8} {'Tgt':<8} {'Exit':<8} {'P&L%':<9} {'St':<5} {'Rsn':<7} {'Time':<6} {'Depth':<6} {'ATR':<5} {'Src':<4} {'OI':<6}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for t in trades:
        print(f"  {t.day:<12} {t.trade_num:<3} {t.signal:<4} {t.direction:<8} {t.strike:<7} "
              f"{t.entry_premium:<8.1f} {t.sl_premium:<8.1f} {t.target_premium:<8.1f} "
              f"{t.exit_premium:<8.1f} {t.pnl_pct:>+7.1f}% {t.status:<5} "
              f"{t.exit_reason:<7} {t.entry_time:<6} {t.swing_depth:>5.0f} {t.atr:>5.1f} {t.data_source:<4} {t.oi_filter:<6}")

    # Signal breakdown: CHC vs CLC
    print()
    print("  Signal Breakdown:")
    for sig in ("CHC", "CLC"):
        sig_trades = [t for t in trades if t.signal == sig and t.status in ("WON", "LOST")]
        if sig_trades:
            w = sum(1 for t in sig_trades if t.status == "WON")
            pnl = sum(t.pnl_pct for t in sig_trades)
            label = "BUY CE" if sig == "CHC" else "BUY PE"
            print(f"    {sig} ({label:<7}): "
                  f"{len(sig_trades)} trades  W={w} L={len(sig_trades)-w}  "
                  f"WR={w/len(sig_trades)*100:.0f}%  P&L={pnl:+.1f}%")

    # OI Filter Impact (CHC only)
    print()
    print("  OI Filter Impact (CHC only):")
    chc_all = [t for t in trades if t.signal == "CHC" and t.status in ("WON", "LOST")]
    if chc_all:
        w = sum(1 for t in chc_all if t.status == "WON")
        print(f"    CHC trades taken: {len(chc_all)}, WR={w/len(chc_all)*100:.0f}%")
        by_filter = defaultdict(list)
        for t in chc_all:
            by_filter[t.oi_filter or "none"].append(t)
        for f, tl in sorted(by_filter.items()):
            fw = sum(1 for t in tl if t.status == "WON")
            print(f"    {f}: {len(tl)} trades, WR={fw/len(tl)*100:.0f}%")
    else:
        print("    No CHC trades")

    # Closing time analysis
    print()
    print("  Exit Time Distribution:")
    exit_hours = defaultdict(list)
    for t in trades:
        if t.exit_time and t.status in ("WON", "LOST"):
            hour = t.exit_time[:2]
            exit_hours[hour].append(t)
    for h in sorted(exit_hours):
        tlist = exit_hours[h]
        w = sum(1 for x in tlist if x.status == "WON")
        print(f"    {h}:xx  {len(tlist)} trades  W={w} L={len(tlist)-w}  "
              f"WR={w/len(tlist)*100:.0f}%")


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(args):
    global DEBUG
    DEBUG = args.debug

    print("=" * 95)
    print("  PA V3: Swing Reversal + CHC/CLC on 1-Min Nifty")
    print("=" * 95)
    print()
    print(f"  Params: swing_n={SWING_N} (CLOSE-based), atomic 5-candle pattern")
    print(f"  Depth filter: >= {MIN_SWING_DEPTH_ATR}x ATR({ATR_PERIOD}) or >= {MIN_SWING_DEPTH_PTS}pts")
    print(f"  Min gap between same-type swings: {MIN_SWING_GAP} candles")
    print(f"  Window: {args.start} - {PA_END}, EOD exit: {EOD_EXIT}")
    print(f"  Good PA: range >= {GOOD_PA_MIN_RANGE_PCT}% over {GOOD_PA_LOOKBACK} candles")
    print(f"  Min premium: {MIN_PREMIUM}, Min risk: {MIN_RISK_PCT:.0%}")
    oi_filter_mode = "disabled" if args.no_oi_filter else ("strict" if args.strict_oi else "standard")
    print(f"  OI Filter: {oi_filter_mode}")
    print()

    # 1. Connect to Kite
    print("  [1/4] Connecting to Kite...")
    kite = connect_kite()

    # 2. Fetch NIFTY 1-min data
    to_date = date.today()
    from_date = to_date - timedelta(days=args.days + 10)  # Extra days for weekends
    print(f"\n  [2/4] Fetching NIFTY 1-min data ({from_date} to {to_date})...")
    nifty_1m = fetch_nifty_1min(kite, from_date, to_date)

    if not nifty_1m:
        print("  ERROR: No NIFTY data fetched. Check Kite auth.")
        return

    # Group by day
    nifty_by_day = group_candles_by_day(nifty_1m)
    trading_days = sorted(nifty_by_day.keys())[-args.days:]
    print(f"  Trading days: {len(trading_days)} ({trading_days[0]} to {trading_days[-1]})")

    # 3. Fetch option instruments and data
    print(f"\n  [3/4] Fetching option data...")

    # Determine ATM strikes needed per day
    day_atm_info = {}  # day -> {atm, expiry, strikes_needed}
    for day in trading_days:
        candles = nifty_by_day[day]
        opening = candles[0].open
        atm = find_atm(opening)
        expiry = get_weekly_expiry(date.fromisoformat(day))
        # ATM and neighbors
        strikes_needed = [atm - NIFTY_STEP, atm, atm + NIFTY_STEP]
        day_atm_info[day] = {"atm": atm, "expiry": expiry, "strikes": strikes_needed}

    opt_cache: Dict[Tuple[int, str, str], List[Candle]] = {}  # (strike, type, expiry) -> candles

    if args.db_only:
        print("  --db-only: Skipping Kite option fetch, using DB fallback only")
    else:
        token_map = get_option_tokens(kite)

        # Collect unique instruments to fetch
        instruments_to_fetch = set()  # (strike, type, expiry, token)
        for day, info in day_atm_info.items():
            for strike in info["strikes"]:
                for otype in ("CE", "PE"):
                    key = (strike, otype, info["expiry"])
                    if key in token_map:
                        instruments_to_fetch.add((strike, otype, info["expiry"], token_map[key]))

        print(f"  Unique option instruments to fetch: {len(instruments_to_fetch)}")

        # Fetch option 3-min data grouped by expiry
        fetched = 0
        for strike, otype, expiry, token in instruments_to_fetch:
            # Determine date range for this instrument
            exp_date = date.fromisoformat(expiry)
            inst_from = exp_date - timedelta(days=7)  # Week before expiry
            inst_to = exp_date

            candles = fetch_option_3min(kite, token, inst_from, inst_to)
            if candles:
                opt_cache[(strike, otype, expiry)] = candles
                fetched += 1

            time.sleep(0.35)  # Rate limiting
            if fetched % 10 == 0 and fetched > 0:
                print(f"    Fetched {fetched}/{len(instruments_to_fetch)} instruments...")

        print(f"  Fetched option data for {fetched} instruments from Kite")

    # Load OI analysis data
    print(f"\n  Loading OI analysis data...")
    oi_data_by_day = {}
    days_with_oi = 0
    for day in trading_days:
        rows = load_analysis_history_for_day(day)
        pats = load_pm_patterns_for_day(day)
        oi_data_by_day[day] = (rows if rows else None, pats if pats else None)
        if rows:
            days_with_oi += 1
    print(f"  OI data for {days_with_oi}/{len(trading_days)} days")

    # 4. Run simulation
    print(f"\n  [4/4] Running simulation...")

    all_trades_1 = []  # max 1 trade/day
    all_trades_3 = []  # max 3 trades/day
    days_with_data = 0
    days_no_opt = 0
    days_kite = 0
    days_db = 0

    for day in trading_days:
        day_1m = nifty_by_day[day]
        info = day_atm_info[day]

        # Build option data dict for this day
        opt_day: Dict[Tuple[int, str], List[Candle]] = {}
        data_source = "none"

        for strike in info["strikes"]:
            for otype in ("CE", "PE"):
                key3 = (strike, otype, info["expiry"])
                if key3 in opt_cache:
                    # Filter candles to this day only
                    day_candles = [c for c in opt_cache[key3]
                                   if c.dt.strftime("%Y-%m-%d") == day]
                    if day_candles:
                        opt_day[(strike, otype)] = day_candles
                        data_source = "kite"

                # Database fallback
                if (strike, otype) not in opt_day:
                    db_candles = load_db_option_candles(day, strike, otype)
                    if db_candles:
                        opt_day[(strike, otype)] = db_candles
                        if data_source == "none":
                            data_source = "db"

        if not opt_day:
            days_no_opt += 1
            continue
        days_with_data += 1
        if data_source == "kite":
            days_kite += 1
        elif data_source == "db":
            days_db += 1

        # Simulate for 1 trade/day and 3 trades/day
        oi_rows, oi_pats = oi_data_by_day.get(day, (None, None))
        t1 = simulate_day(day_1m, opt_day, day, max_trades=1, pa_start=args.start,
                          analysis_rows=oi_rows, pm_patterns=oi_pats, oi_filter_mode=oi_filter_mode)
        t3 = simulate_day(day_1m, opt_day, day, max_trades=3, pa_start=args.start,
                          analysis_rows=oi_rows, pm_patterns=oi_pats, oi_filter_mode=oi_filter_mode)
        all_trades_1.extend(t1)
        all_trades_3.extend(t3)

        if DEBUG and (t1 or t3):
            for t in t1:
                print(f"  {day}: {t.direction} {t.strike}{t.option_type} "
                      f"@ {t.entry_premium:.1f} -> {t.status} ({t.pnl_pct:+.1f}%)")

    print(f"\n  Days with option data: {days_with_data}/{len(trading_days)} "
          f"(kite: {days_kite}, db: {days_db}, no data: {days_no_opt})")

    # Results
    print_stats("PA V3: 1 TRADE PER DAY", all_trades_1)
    print_stats("PA V3: UP TO 3 TRADES PER DAY", all_trades_3)

    # Head-to-head comparison
    s1 = compute_stats(all_trades_1)
    s3 = compute_stats(all_trades_3)
    if s1 and s3:
        print()
        print("=" * 95)
        print("  HEAD-TO-HEAD: 1 Trade/Day vs 3 Trades/Day")
        print("=" * 95)
        print()
        metrics = [
            ("Win Rate", f"{s1['wr']:.1f}%", f"{s3['wr']:.1f}%"),
            ("Total P&L", f"{s1['pnl']:+.1f}%", f"{s3['pnl']:+.1f}%"),
            ("Trade Count", f"{s1['total']}", f"{s3['total']}"),
            ("Profit Factor", f"{s1['pf']:.2f}", f"{s3['pf']:.2f}"),
            ("Max Drawdown", f"{s1['max_dd']:.1f}%", f"{s3['max_dd']:.1f}%"),
            ("Max Consec Loss", f"{s1['max_cl']}", f"{s3['max_cl']}"),
            ("Avg Win", f"{s1['avg_win']:+.1f}%", f"{s3['avg_win']:+.1f}%"),
            ("Avg Loss", f"{s1['avg_loss']:+.1f}%", f"{s3['avg_loss']:+.1f}%"),
        ]
        print(f"  {'Metric':<20} {'1/Day':<14} {'3/Day':<14}")
        print("  " + "-" * 48)
        for name, v1, v3 in metrics:
            print(f"  {name:<20} {v1:<14} {v3:<14}")

    print()
    print("=" * 95)
    print(f"  Backtest: {len(trading_days)} trading days ({trading_days[0]} to {trading_days[-1]})")
    print(f"  Data sources: Kite API (1-min NIFTY + 3-min options) + DB fallback")
    print("=" * 95)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PA V3 Swing Strategy Backtest")
    parser.add_argument("--days", type=int, default=30, help="Trading days to test (default: 30)")
    parser.add_argument("--debug", action="store_true", help="Show detailed signal info")
    parser.add_argument("--start", default="09:30", help="PA start time (default: 09:30)")
    parser.add_argument("--db-only", action="store_true", help="Use DB option data only (skip Kite option fetch)")
    parser.add_argument("--no-oi-filter", action="store_true", help="Disable OI filter")
    parser.add_argument("--strict-oi", action="store_true", help="Strict OI filter")
    run(parser.parse_args())
