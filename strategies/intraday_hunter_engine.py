"""IntradayHunter signal detection engine — pure Python, no DB writes.

Mirrors scripts/backtest_intraday_hunter.py v5 final logic at runtime.
The engine is fed live 1-min candle buffers (typically from CandleBuilder)
and emits BUY/SELL signal dicts. The strategy file (intraday_hunter.py)
handles the trade lifecycle, DB writes, and broker orders.

Backtest provenance: 19/23 video alignment, PF 1.25, WR 47.1%, MDD Rs 58K
across 563 trading days (Jan 2024 → Apr 2026, 1-lot sizing).
See docs/strategy_research/STRATEGY_RESEARCH.md for full history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Dict, List, Optional, Tuple

from config import IntradayHunterConfig
from core.logger import get_logger
from kite.iv import black_scholes_price

log = get_logger("ih_engine")


# ── Index meta ──────────────────────────────────────────────────────────

# Strike spacing for ATM option lookup
STRIKE_SPACING = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "SENSEX": 100,
}

# Round-number step for the trader's "decent target" rule
ROUND_NUMBER_INCREMENT = {
    "NIFTY": 500,
    "BANKNIFTY": 500,
    "SENSEX": 1000,
}

# Weekly expiry day-of-week (Mon=0..Sun=6)
EXPIRY_DOW = {
    "NIFTY":     1,  # Tuesday
    "BANKNIFTY": 2,  # Wednesday (legacy; treat as approximation)
    "SENSEX":    1,  # Tuesday
}


# ── Candle adapter ──────────────────────────────────────────────────────

@dataclass
class Candle:
    """Lightweight candle wrapper.

    The runtime CandleBuilder produces dicts; we convert to this dataclass
    so the signal logic stays pure (no dict-key probing).
    """
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    @property
    def is_green(self) -> bool:
        return self.close >= self.open

    @classmethod
    def from_dict(cls, d: dict) -> "Candle":
        ts = d.get("date") or d.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                ts = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return cls(
            ts=ts,
            open=float(d["open"]),
            high=float(d["high"]),
            low=float(d["low"]),
            close=float(d["close"]),
            volume=int(d.get("volume", 0)),
        )


def candles_from_dicts(rows: List[dict]) -> List[Candle]:
    return [Candle.from_dict(r) for r in rows]


# ── Helper functions ────────────────────────────────────────────────────

def atm_strike(spot: float, spacing: int) -> float:
    return round(spot / spacing) * spacing


def days_to_next_expiry(d: date, dow: int) -> int:
    today_dow = d.weekday()
    diff = (dow - today_dow) % 7
    return diff if diff != 0 else 7


def model_premium(
    spot: float,
    strike: float,
    option_type: str,
    days_to_expiry: int,
    iv: float,
    cfg: IntradayHunterConfig,
) -> float:
    """Black-Scholes ATM option premium estimate."""
    t_years = days_to_expiry / 365.0
    return black_scholes_price(
        spot=spot, strike=strike, t=t_years,
        r=cfg.RISK_FREE_RATE, sigma=iv, option_type=option_type,
    )


def iv_for_index(index_label: str, vix_pct: Optional[float], cfg: IntradayHunterConfig) -> float:
    """Resolve IV for the index from current VIX (or fallback to default).

    vix_pct: India VIX (e.g., 13.5 → 0.135).
    """
    base = (vix_pct / 100.0) if vix_pct and vix_pct > 0 else cfg.DEFAULT_IV
    if index_label == "BANKNIFTY":
        return base * cfg.BN_IV_SCALE
    if index_label == "SENSEX":
        return base * cfg.SX_IV_SCALE
    return base


def compute_gap_pct(today: List[Candle], yesterday: Optional[List[Candle]]) -> float:
    if not yesterday or not today:
        return 0.0
    return (today[0].open - yesterday[-1].close) / yesterday[-1].close * 100


def compute_current_move_pct(today: List[Candle], minute_idx: int) -> float:
    if not today or minute_idx <= 0:
        return 0.0
    cur_idx = min(minute_idx, len(today) - 1)
    return (today[cur_idx].close - today[0].open) / today[0].open * 100


# ── E0: gap-rejection-recovery (the trader's favorite gap-day pattern) ─

def detect_e0(
    minute_idx: int,
    today: List[Candle],
    yesterday: Optional[List[Candle]],
    cfg: IntradayHunterConfig,
) -> Optional[str]:
    """E0: gap-rejection-recovery.

    Detects:
      1. Yesterday was directional (|move| >= E0_MIN_YDAY_PCT)
      2. Today's first candle moves against yesterday's direction
      3. Subsequent candles recover at least E0_MIN_RECOVERY_PCT toward
         yesterday's direction

    Fires in YESTERDAY's direction (= counter to the initial rejection).
    Only fires between E0_ENTRY_START and E0_MAX_MINUTE (early window),
    because the setup is transient — by 09:35 the recovery has usually
    completed and the move is extended.

    Backtest provenance: +Rs 11,918 over 2.3 years (V1 default config).
    Mirrors scripts/backtest_intraday_hunter.detect_e0.
    """
    if not cfg.ENABLE_E0 or not yesterday or not today:
        return None
    if minute_idx < 2 or minute_idx > cfg.E0_MAX_MINUTE:
        return None

    # Must be inside the E0-specific entry window
    if minute_idx >= len(today):
        return None
    now = today[minute_idx].ts.time()
    if now < cfg.E0_ENTRY_START:
        return None

    # 1. Yesterday must be directional
    y_open = yesterday[0].open
    y_close = yesterday[-1].close
    if y_open <= 0:
        return None
    y_move_pct = (y_close - y_open) / y_open * 100
    if abs(y_move_pct) < cfg.E0_MIN_YDAY_PCT:
        return None
    y_bullish = y_move_pct > 0

    # 2. First-candle move against yesterday's direction
    first = today[0]
    if first.open <= 0:
        return None
    first_move_pct = (first.close - first.open) / first.open * 100
    if y_bullish and first_move_pct > -cfg.E0_MIN_INITIAL_PCT:
        return None  # yesterday bullish but first candle not red enough
    if not y_bullish and first_move_pct < cfg.E0_MIN_INITIAL_PCT:
        return None  # yesterday bearish but first candle not green enough

    # 3. Recovery: current candle must have moved back in yday direction
    cur = today[minute_idx]
    if first.close <= 0:
        return None
    recovery_pct = (cur.close - first.close) / first.close * 100
    if y_bullish:
        if recovery_pct < cfg.E0_MIN_RECOVERY_PCT:
            return None
        return "BUY"
    else:
        if recovery_pct > -cfg.E0_MIN_RECOVERY_PCT:
            return None
        return "SELL"


# ── E1: rejection after directional run ─────────────────────────────────

def detect_e1(minute_idx: int, candles: List[Candle], cfg: IntradayHunterConfig) -> Optional[str]:
    """E1: small retracement after a directional run.

    `minute_idx` is the EXCLUSIVE upper bound for the retracement slice
    (i.e. retr = candles[run_end:minute_idx]). It is valid for it to
    equal len(candles). It is NOT valid for it to exceed len(candles) —
    we cap it down so a stale minute_idx doesn't IndexError.

    Returns 'BUY' / 'SELL' / None.
    """
    min_lookback = cfg.E1_RUN_LENGTH + cfg.E1_MAX_RETR_CANDLES
    if minute_idx < min_lookback:
        return None
    # Cap to actual buffer length (different indices may have different
    # buffer sizes during the morning bootstrap window).
    if minute_idx > len(candles):
        minute_idx = len(candles)
        if minute_idx < min_lookback:
            return None

    for retr_len in range(cfg.E1_MIN_RETR_CANDLES, cfg.E1_MAX_RETR_CANDLES + 1):
        run_end = minute_idx - retr_len
        run_start = run_end - cfg.E1_RUN_LENGTH
        if run_start < 0:
            continue

        run = candles[run_start:run_end]
        retr = candles[run_end:minute_idx]
        # Defensive: slices must match expected lengths exactly.
        if len(run) != cfg.E1_RUN_LENGTH or len(retr) != retr_len:
            continue

        all_green = all(c.is_green for c in run)
        all_red = all(not c.is_green for c in run)
        if not (all_green or all_red):
            continue

        run_direction = "BUY" if all_green else "SELL"
        run_pts = abs(run[-1].close - run[0].open)
        if run_pts <= 0:
            continue

        retr_pts = abs(retr[-1].close - run[-1].close)
        retr_dir_correct = (
            (run_direction == "BUY" and retr[-1].close < run[-1].close)
            or (run_direction == "SELL" and retr[-1].close > run[-1].close)
        )
        if not retr_dir_correct:
            continue
        if retr_pts / run_pts > cfg.E1_RETRACEMENT_MAX_PCT:
            continue

        return run_direction
    return None


# ── E2: gap counter-trap ────────────────────────────────────────────────

def detect_e2(
    minute_idx: int,
    today: List[Candle],
    yesterday: Optional[List[Candle]],
    cfg: IntradayHunterConfig,
) -> Optional[str]:
    """E2: gap counter-trap.

    Gap-down + price hasn't broken yesterday's low after wait_minutes → BUY.
    Gap-up + price hasn't broken yesterday's high after wait_minutes → SELL.
    """
    if not cfg.E2_ENABLED or not yesterday or not today:
        return None
    if minute_idx < cfg.E2_WAIT_MINUTES:
        return None
    if minute_idx > len(today):
        minute_idx = len(today)
        if minute_idx < cfg.E2_WAIT_MINUTES:
            return None

    gap_pct = compute_gap_pct(today, yesterday)
    if abs(gap_pct) < cfg.E2_MIN_GAP_PCT:
        return None

    y_high = max(c.high for c in yesterday)
    y_low = min(c.low for c in yesterday)

    window = today[: minute_idx + 1]
    if not window:
        return None
    window_high = max(c.high for c in window)
    window_low = min(c.low for c in window)

    if gap_pct < 0 and window_low > y_low:
        return "BUY"
    if gap_pct > 0 and window_high < y_high:
        return "SELL"
    return None


# ── E3: trend continuation / slow drift ─────────────────────────────────

def detect_e3(
    minute_idx: int,
    today: List[Candle],
    yesterday: Optional[List[Candle]],
    cfg: IntradayHunterConfig,
) -> Optional[str]:
    """E3: slow trend continuation. Catches grinding days that E1/E2 miss."""
    if not cfg.E3_ENABLED or not yesterday or not today:
        return None
    if minute_idx < cfg.E3_WAIT_MINUTES:
        return None
    if minute_idx >= len(today):
        # E3 dereferences today[minute_idx], so cap to len-1 here (NOT len)
        minute_idx = len(today) - 1
        if minute_idx < cfg.E3_WAIT_MINUTES:
            return None

    y_open = yesterday[0].open
    y_close = yesterday[-1].close
    y_move_pct = (y_close - y_open) / y_open * 100
    if abs(y_move_pct) < cfg.E3_MIN_YCLOSE_PCT:
        return None

    today_open = today[0].open
    gap_pct = (today_open - y_close) / y_close * 100
    if abs(gap_pct) < cfg.E3_MIN_GAP_PCT:
        return None

    bullish = y_move_pct > 0 and gap_pct > 0
    bearish = y_move_pct < 0 and gap_pct < 0
    if not (bullish or bearish):
        return None

    cur = today[minute_idx]
    cur_move_pct = (cur.close - today_open) / today_open * 100
    if abs(cur_move_pct) > cfg.E3_MAX_CURRENT_PCT:
        return None

    if bullish and cur.close > today_open and cur.close > y_close:
        return "BUY"
    if bearish and cur.close < today_open and cur.close < y_close:
        return "SELL"
    return None


# ── Filters ─────────────────────────────────────────────────────────────

def constituent_confluence_check(
    direction: str,
    minute_idx: int,
    hdfc_today: List[Candle],
    kotak_today: List[Candle],
    cfg: IntradayHunterConfig,
) -> bool:
    """R29: HDFC + KOTAK should not BOTH diverge from a BN signal.

    Returns True if BN entry is OK, False if it should be skipped.
    """
    if not cfg.ENABLE_CONSTITUENT_CONFLUENCE:
        return True
    if not hdfc_today or not kotak_today:
        return True

    end_idx = min(minute_idx, len(hdfc_today) - 1, len(kotak_today) - 1)
    if end_idx <= 0:
        return True

    h = (hdfc_today[end_idx].close - hdfc_today[0].open) / hdfc_today[0].open * 100
    k = (kotak_today[end_idx].close - kotak_today[0].open) / kotak_today[0].open * 100
    threshold = cfg.CONSTITUENT_MIN_PCT

    if direction == "BUY" and h <= -threshold and k <= -threshold:
        return False
    if direction == "SELL" and h >= threshold and k >= threshold:
        return False
    return True


def constituent_internal_split(
    minute_idx: int,
    hdfc_today: List[Candle],
    kotak_today: List[Candle],
    cfg: IntradayHunterConfig,
) -> bool:
    """Returns True when HDFC and KOTAK strongly disagree with EACH OTHER.

    Used to skip the BN component only (not the whole signal).
    """
    if not cfg.ENABLE_CONSTITUENT_INTERNAL_SPLIT:
        return False
    if not hdfc_today or not kotak_today:
        return False
    end_idx = min(minute_idx, len(hdfc_today) - 1, len(kotak_today) - 1)
    if end_idx <= 0:
        return False
    h = (hdfc_today[end_idx].close - hdfc_today[0].open) / hdfc_today[0].open * 100
    k = (kotak_today[end_idx].close - kotak_today[0].open) / kotak_today[0].open * 100
    return abs(h - k) >= cfg.CONSTITUENT_INTERNAL_SPLIT_PCT


def compute_day_bias_score(
    today: List[Candle],
    yesterday: Optional[List[Candle]],
    minute_idx: int,
    hdfc_today: List[Candle],
    kotak_today: List[Candle],
    cfg: IntradayHunterConfig,
) -> float:
    """Composite day-bias score in [-1, 1].

    Weighted blend of yesterday's move + today's gap + intraday move + HDFC + KOTAK.
    """
    if not yesterday or not today:
        return 0.0

    threshold = cfg.DAY_BIAS_INPUT_THRESHOLD_PCT

    def clip(pct: float) -> float:
        return max(-1.0, min(1.0, pct / threshold))

    y_open = yesterday[0].open
    y_close = yesterday[-1].close
    y_move = (y_close - y_open) / y_open * 100 if y_open > 0 else 0.0

    gap = (today[0].open - y_close) / y_close * 100 if y_close > 0 else 0.0

    cur_idx = min(minute_idx, len(today) - 1)
    intraday = (today[cur_idx].close - today[0].open) / today[0].open * 100 if cur_idx > 0 else 0.0

    h_move = k_move = 0.0
    if hdfc_today:
        h_idx = min(minute_idx, len(hdfc_today) - 1)
        if h_idx > 0:
            h_move = (hdfc_today[h_idx].close - hdfc_today[0].open) / hdfc_today[0].open * 100
    if kotak_today:
        k_idx = min(minute_idx, len(kotak_today) - 1)
        if k_idx > 0:
            k_move = (kotak_today[k_idx].close - kotak_today[0].open) / kotak_today[0].open * 100

    # [V2] Multi-day regime: blend in yesterday's close-position-within-range.
    # Close near high (>= 80%) → bullish (+1); close near low (<= 20%) → -1.
    # Adds an extra signal beyond just yesterday's net move — captures
    # "strong directional close" vs "doji/chop close".
    if cfg.ENABLE_MULTI_DAY_REGIME:
        y_high = max(c.high for c in yesterday)
        y_low = min(c.low for c in yesterday)
        y_range = y_high - y_low
        if y_range > 0:
            pos = (y_close - y_low) / y_range  # 0.0 to 1.0
            y_close_pos = (pos - 0.5) * 2       # -1.0 to +1.0
        else:
            y_close_pos = 0.0
        # Rebalanced weights: pull 0.05 from y_move + 0.05 from intraday for close_pos
        score = (
            0.15 * clip(y_move)
          + 0.10 * y_close_pos              # NEW V2 input
          + 0.25 * clip(gap)
          + 0.30 * clip(intraday)
          + 0.10 * clip(h_move)
          + 0.10 * clip(k_move)
        )
    else:
        score = (
            0.20 * clip(y_move)
          + 0.30 * clip(gap)
          + 0.30 * clip(intraday)
          + 0.10 * clip(h_move)
          + 0.10 * clip(k_move)
        )
    return max(-1.0, min(1.0, score))


def filter_day_bias(
    direction: str,
    score: float,
    cfg: IntradayHunterConfig,
) -> bool:
    """Soft veto: only block if |score| >= block_threshold AND opposite direction."""
    if not cfg.ENABLE_DAY_BIAS:
        return True
    threshold = cfg.DAY_BIAS_BLOCK_SCORE
    if direction == "BUY" and score <= -threshold:
        return False
    if direction == "SELL" and score >= threshold:
        return False
    return True


# ── The main detect function ────────────────────────────────────────────

@dataclass
class Signal:
    """A confirmed signal ready for trade creation."""
    direction: str           # 'BUY' or 'SELL'
    trigger: str             # 'E1' / 'E2' / 'E3'
    minute_idx: int
    day_bias_score: float
    skip_bn: bool            # if True, only enter NIFTY + SENSEX
    notes: str = ""


class IntradayHunterEngine:
    """Stateless signal detection. Call detect() each minute with current candles."""

    def __init__(self, config: Optional[IntradayHunterConfig] = None):
        self.cfg = config or IntradayHunterConfig()

    # ------------------------------------------------------------------
    # Single-minute signal detection
    # ------------------------------------------------------------------

    def detect(
        self,
        minute_idx: int,
        nifty_today: List[Candle],
        bn_today: List[Candle],
        sx_today: List[Candle],
        nifty_yesterday: Optional[List[Candle]],
        hdfc_today: Optional[List[Candle]] = None,
        kotak_today: Optional[List[Candle]] = None,
    ) -> Optional[Signal]:
        """Run all triggers + filters at one minute. Returns a Signal or None.

        Mirrors detect_signals() in the backtester. The order is:
            1. E1 on each of NIFTY/BN/SX (need 2-of-3 multi-index agreement)
            2. E2 on NIFTY (gap counter-trap, fires unconditionally)
            3. E3 on NIFTY (trend continuation, fires unconditionally)
            4. Day-bias soft-veto
            5. R29 HDFC+KOTAK confluence (rejects whole signal on divergence)
            6. R29 internal-split (skips BN only on HDFC↔KOTAK split)
        """
        cfg = self.cfg
        hdfc_today = hdfc_today or []
        kotak_today = kotak_today or []

        # Stage 1: E1 multi-index
        e1_by_side: Dict[str, List[str]] = {"BUY": [], "SELL": []}
        for label, cs in (("NIFTY", nifty_today), ("BANKNIFTY", bn_today), ("SENSEX", sx_today)):
            side = detect_e1(minute_idx, cs, cfg)
            if side:
                e1_by_side[side].append(label)

        # Stage 2 + 3: E2, E3 on NIFTY
        e2_side = detect_e2(minute_idx, nifty_today, nifty_yesterday, cfg)
        e3_side = detect_e3(minute_idx, nifty_today, nifty_yesterday, cfg)

        # [V1] Stage 0: E0 (gap-rejection-recovery, early window 09:17-09:25)
        e0_side = detect_e0(minute_idx, nifty_today, nifty_yesterday, cfg)

        # Build candidate list (E0 then E2 then E3 then E1)
        candidates: List[Tuple[str, str]] = []  # (trigger, direction)
        if e0_side:
            candidates.append(("E0", e0_side))
        if e2_side:
            candidates.append(("E2", e2_side))
        if e3_side:
            candidates.append(("E3", e3_side))
        for side, indices in e1_by_side.items():
            if len(indices) >= cfg.MULTI_INDEX_MIN:
                candidates.append(("E1", side))

        if not candidates:
            return None

        # Compute day-bias score (will be returned in the Signal)
        score = compute_day_bias_score(
            nifty_today, nifty_yesterday, minute_idx,
            hdfc_today, kotak_today, cfg,
        )

        for trigger, direction in candidates:
            # Day-bias soft-veto
            if not filter_day_bias(direction, score, cfg):
                continue
            # R29 confluence
            if not constituent_confluence_check(
                direction, minute_idx, hdfc_today, kotak_today, cfg
            ):
                continue
            # Internal-split → skip BN only
            skip_bn = constituent_internal_split(minute_idx, hdfc_today, kotak_today, cfg)
            return Signal(
                direction=direction,
                trigger=trigger,
                minute_idx=minute_idx,
                day_bias_score=score,
                skip_bn=skip_bn,
                notes=f"trigger={trigger} score={score:+.2f}",
            )
        return None

    # ------------------------------------------------------------------
    # Position sizing helper — used by the strategy at trade-creation
    # ------------------------------------------------------------------

    def build_position_set(
        self,
        signal: Signal,
        nifty_spot: float,
        bn_spot: float,
        sx_spot: float,
        today: date,
        vix_pct: Optional[float],
        candle_builder: Optional[Any] = None,
        live_mode: bool = False,
    ) -> List[dict]:
        """Compute the 3-position set for a confirmed signal.

        Returns a list of dicts ready for trade-table insertion. Each dict has:
            index_label, direction, strike, option_type, qty,
            entry_premium, sl_premium, target_premium, iv

        If signal.skip_bn is True, the BANKNIFTY entry is omitted (returns 2).

        candle_builder: optional CandleBuilder. When provided, the entry
            premium is taken from the latest 1-min close of the actual
            option strike (real LTP) instead of Black-Scholes. Falls back
            to BS if the strike isn't subscribed yet (e.g. SX on a fresh
            startup before the 3-min rotation).
        """
        cfg = self.cfg
        otype = "CE" if signal.direction == "BUY" else "PE"
        positions: List[dict] = []

        index_data = [
            ("NIFTY", nifty_spot, cfg.NIFTY_QTY),
            ("BANKNIFTY", bn_spot, cfg.BANKNIFTY_QTY),
            ("SENSEX", sx_spot, cfg.SENSEX_QTY),
        ]

        for label, spot, qty in index_data:
            if label == "BANKNIFTY" and signal.skip_bn:
                continue
            if spot <= 0 or qty <= 0:
                continue
            strike = int(atm_strike(spot, STRIKE_SPACING[label]))
            dte = days_to_next_expiry(today, EXPIRY_DOW[label])
            iv = iv_for_index(label, vix_pct, cfg)

            # Real LTP path: try CandleBuilder for the actual option strike.
            # Pre-emptive rotation in scheduler subscribes ATM±1 strikes for
            # NIFTY/BN/SX every 3 minutes, so the buffer normally has the
            # current ATM strike's LTP available.
            premium = 0.0
            premium_source = "BS"
            if candle_builder is not None:
                try:
                    strike_label = f"{label}_{strike}_{otype}"
                    cs = candle_builder.get_candles(strike_label, "1min")
                    if cs:
                        last_close = float(cs[-1].get("close", 0) or 0)
                        if last_close > 0:
                            premium = last_close
                            premium_source = "LTP"
                except Exception:
                    pass

            # Fallback: Black-Scholes (backtester only).
            if premium <= 0:
                if live_mode:
                    log.warning("IH: no real LTP, skipping position",
                                index=label, strike=strike, type=otype)
                    continue
                premium = model_premium(spot, strike, otype, dte, iv, cfg)
                if premium <= 0:
                    continue

            positions.append({
                "index_label": label,
                "direction": signal.direction,
                "strike": strike,
                "option_type": otype,
                "qty": qty,
                "entry_premium": round(premium, 2),
                "sl_premium": round(premium * (1 - cfg.SL_PCT), 2),
                "target_premium": round(premium * (1 + cfg.TGT_PCT), 2),
                "iv": round(iv, 4),
                "premium_source": premium_source,
            })
        return positions
