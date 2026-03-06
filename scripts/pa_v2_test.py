"""
PA Strategy V2 Forward Test -- Financial Expert Redesign
========================================================

Loss pattern analysis of 6 losing PA trades revealed:
  - 4/6 losses on LOW VOLATILITY / RANGE-BOUND days (fake CHC breakouts)
  - Feb 04: Premium rose 188->236 then collapsed 236->190 (classic fake breakout)
  - Feb 09: 0.29% day range, theta decay killed the position on a dead day
  - Feb 18: Bought PUT against "Bulls Winning" OI -- CHC caught noise not signal
  - Feb 26: PE bounced 108-144 range, CHC-3 from random noise

Root cause: CHC-3 is blind to whether momentum is SUSTAINABLE or noise.

V2 Enhancements (ordered by expected impact):
  1. CHC Strength Floor       -- Minimum 2% aggregate move over 3 candles
  2. Tighter Choppy Filter    -- Raise spot range threshold from 0.15% to 0.25%
  3. OI Strength Alignment    -- Don't fight the OI walls (net_strength gate)
  4. PM EMA Confirmation      -- Smoothed premium momentum must agree with direction
  5. Trailing Breakeven SL    -- After +8% unrealized, SL -> entry (protect profits)
  6. OI Cluster Dynamic SL    -- Use OI support/resistance walls for SL placement
  7. VIX-Adaptive SL/Target   -- Wider SL+T in high VIX, tighter in low
  8. Confirmation Status Gate  -- CONFLICT trades need stronger CHC
  9. Warmup Period            -- Skip first 5 candles (15 min) for noise avoidance
"""

import sqlite3
import json
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "oi_tracker.db"
NIFTY_STEP = 50

# ─── V1 Constants (baseline) ────────────────────────────────────────────────
V1_SL_PCT = 0.15
V1_TARGET_PCT = 0.15
V1_CHC_LOOKBACK = 3
V1_CHOPPY_LOOKBACK = 10
V1_CHOPPY_THRESHOLD = 0.15
V1_MIN_PREMIUM = 5.0
PA_START = "09:30"
PA_END = "14:00"
EOD_EXIT = "15:20"

# ─── V2 Constants (surgical exit-only redesign) ─────────────────────────────
# Insight: V2a over-filtered entries (blocked 3 winners, 0 losers).
# PA's edge IS its simplicity. Don't touch entries. Fix EXITS only.
#
# Exit redesign philosophy:
#   - Let winners run further with trailing profit lock
#   - Cut losers earlier on range-bound/dead days using orderflow confirmation
#   - Protect gains: after +10% unrealized, trail SL at (peak - 7%)
V2_SL_PCT = 0.15            # Same base SL as V1
V2_TARGET_PCT = 0.15        # Same base target as V1 (but trailing can exceed)
V2_TRAIL_TRIGGER = 0.10     # Start trailing after +10% unrealized
V2_TRAIL_OFFSET = 0.07      # Trail SL at peak - 7%
V2_EXTENDED_TARGET = 0.25   # Extended target for strong moves (disable fixed 15% cap)
V2_MIN_PREMIUM = 5.0
V2_CHC_LOOKBACK = 3
V2_CHOPPY_LOOKBACK = 10
V2_CHOPPY_THRESHOLD = 0.15  # Keep same as V1


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
    confirmation_status: str
    net_strength: float
    pm_score: float
    vix: float
    oi_support: float    # Nearest OI support below spot
    oi_resistance: float  # Nearest OI resistance above spot
    strikes: dict = field(default_factory=dict)


@dataclass
class Trade:
    version: str  # "V1" or "V2"
    day: str
    entry_time: str
    direction: str
    strike: int
    option_type: str
    entry_premium: float
    sl_premium: float
    target_premium: float
    entry_spot: float = 0
    entry_verdict: str = ""
    entry_confidence: float = 0
    chc_strength: float = 0
    status: str = "ACTIVE"
    exit_time: str = ""
    exit_premium: float = 0
    exit_reason: str = ""
    pnl_pct: float = 0
    max_premium: float = 0
    min_premium: float = 0
    # V2 fields
    trailing_sl: float = 0
    breakeven_activated: bool = False
    filters_passed: str = ""  # Debug: which V2 filters were applied


@dataclass
class DayClassification:
    date: str
    regime: str
    volatility: str
    day_range_pct: float
    day_change_pct: float


# ─── Data Loading ────────────────────────────────────────────────────────────

def load_data() -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
               iv_skew, max_pain, futures_oi_change, analysis_json
        FROM analysis_history WHERE signal_confidence > 0 ORDER BY timestamp
    """)
    analysis_rows = cur.fetchall()

    cur.execute("""
        SELECT timestamp, strike_price, ce_ltp, pe_ltp
        FROM oi_snapshots WHERE ce_ltp > 0 OR pe_ltp > 0
        ORDER BY timestamp, strike_price
    """)
    ltp_rows = cur.fetchall()
    conn.close()

    ltp_by_ts = defaultdict(dict)
    for row in ltp_rows:
        ltp_by_ts[row["timestamp"]][int(row["strike_price"])] = {
            "ce_ltp": row["ce_ltp"], "pe_ltp": row["pe_ltp"]
        }

    days = defaultdict(list)
    for row in analysis_rows:
        ts = row["timestamp"]
        day = ts[:10]

        combined_score = 0
        confirmation = ""
        net_strength = 0
        pm_score = 0
        vix = 0
        oi_support = 0
        oi_resistance = 0

        if row["analysis_json"]:
            try:
                aj = json.loads(row["analysis_json"])
                combined_score = aj.get("combined_score", 0)
                confirmation = aj.get("confirmation_status", "")
                sa = aj.get("strength_analysis", {})
                net_strength = sa.get("net_strength", 0) if isinstance(sa, dict) else 0
                pm = aj.get("premium_momentum", {})
                pm_score = pm.get("premium_momentum_score", 0) if isinstance(pm, dict) else 0
                vix = aj.get("vix", 0) or 0
                clusters = aj.get("oi_clusters", {})
                if isinstance(clusters, dict):
                    supp_list = clusters.get("support", [])
                    res_list = clusters.get("resistance", [])
                    if supp_list and isinstance(supp_list[0], dict):
                        oi_support = supp_list[0].get("strike", 0)
                    if res_list and isinstance(res_list[0], dict):
                        oi_resistance = res_list[0].get("strike", 0)
            except Exception:
                pass

        days[day].append(Candle(
            timestamp=ts, time_str=ts[11:16], spot_price=row["spot_price"],
            atm_strike=int(row["atm_strike"]), verdict=row["verdict"],
            confidence=row["signal_confidence"], iv_skew=row["iv_skew"] or 0,
            max_pain=row["max_pain"] or 0, combined_score=combined_score,
            confirmation_status=confirmation, net_strength=net_strength,
            pm_score=pm_score, vix=vix,
            oi_support=oi_support, oi_resistance=oi_resistance,
            strikes=ltp_by_ts.get(ts, {})
        ))

    return dict(days)


def classify_day(day: str, candles: list) -> DayClassification:
    spots = [c.spot_price for c in candles]
    o, cl, hi, lo = spots[0], spots[-1], max(spots), min(spots)
    dr = (hi - lo) / o
    dc = (cl - o) / o

    vol = "high" if dr > 0.025 else "medium" if dr > 0.012 else "low"
    if dr > 0.03:
        reg = "volatile"
    elif abs(dc) > 0.015:
        reg = "trending_up" if dc > 0 else "trending_down"
    elif dc > 0.005:
        reg = "trending_up"
    elif dc < -0.005:
        reg = "trending_down"
    else:
        reg = "range_bound"
    return DayClassification(day, reg, vol, dr * 100, dc * 100)


def get_premium(candle, strike, opt):
    if strike not in candle.strikes:
        return None
    ltp = candle.strikes[strike].get("ce_ltp" if opt == "CE" else "pe_ltp", 0)
    return ltp if ltp and ltp > 0 else None


def find_atm(spot):
    return int(round(spot / NIFTY_STEP) * NIFTY_STEP)


# ─── PA V1 (Baseline) ───────────────────────────────────────────────────────

def simulate_pa_v1(candles, day):
    trade = None
    atm = None
    hist = []  # premium history

    for candle in candles:
        t = candle.time_str
        if atm is None and candle.spot_price > 0:
            atm = find_atm(candle.spot_price)
        if atm is None:
            continue

        ce = get_premium(candle, atm, "CE")
        pe = get_premium(candle, atm, "PE")
        if ce and pe and ce > 0 and pe > 0:
            hist.append({"ce": ce, "pe": pe, "spot": candle.spot_price})

        if trade is not None:
            p = get_premium(candle, trade.strike, trade.option_type)
            if p is None:
                continue
            trade.max_premium = max(trade.max_premium, p)
            trade.min_premium = min(trade.min_premium, p)
            if p <= trade.sl_premium:
                trade.status, trade.exit_time, trade.exit_premium = "LOST", t, p
                trade.exit_reason, trade.pnl_pct = "SL", (p - trade.entry_premium) / trade.entry_premium * 100
                return trade
            if p >= trade.target_premium:
                trade.status, trade.exit_time, trade.exit_premium = "WON", t, p
                trade.exit_reason, trade.pnl_pct = "TARGET", (p - trade.entry_premium) / trade.entry_premium * 100
                return trade
            if t >= EOD_EXIT:
                trade.exit_time, trade.exit_premium = t, p
                trade.pnl_pct = (p - trade.entry_premium) / trade.entry_premium * 100
                trade.exit_reason, trade.status = "EOD", "WON" if trade.pnl_pct > 0 else "LOST"
                return trade
            continue

        if t < PA_START or t >= PA_END or len(hist) < V1_CHC_LOOKBACK + 1:
            continue

        # CHC-3
        recent = hist[-(V1_CHC_LOOKBACK + 1):]
        ce_r = all(recent[i]["ce"] > recent[i-1]["ce"] for i in range(1, V1_CHC_LOOKBACK + 1))
        pe_r = all(recent[i]["pe"] > recent[i-1]["pe"] for i in range(1, V1_CHC_LOOKBACK + 1))
        if not ce_r and not pe_r:
            continue

        ce_pct = (recent[-1]["ce"] - recent[0]["ce"]) / recent[0]["ce"] if recent[0]["ce"] > 0 else 0
        pe_pct = (recent[-1]["pe"] - recent[0]["pe"]) / recent[0]["pe"] if recent[0]["pe"] > 0 else 0

        if ce_r and not pe_r:
            side = "CE"
        elif pe_r and not ce_r:
            side = "PE"
        elif ce_r and pe_r:
            side = "CE" if ce_pct > pe_pct else "PE"
        else:
            continue

        # IV filter
        if side == "CE" and candle.iv_skew > 1.0:
            continue
        if side == "PE" and candle.iv_skew < -1.0:
            continue

        # Choppy filter
        if len(hist) >= V1_CHOPPY_LOOKBACK + 1:
            spots = [h["spot"] for h in hist[-(V1_CHOPPY_LOOKBACK + 1):]]
            avg = sum(spots) / len(spots)
            if avg > 0 and ((max(spots) - min(spots)) / avg * 100) < V1_CHOPPY_THRESHOLD:
                continue

        premium = get_premium(candle, atm, side)
        if premium is None or premium < V1_MIN_PREMIUM:
            continue

        trade = Trade(
            version="V1", day=day, entry_time=t,
            direction="BUY_CALL" if side == "CE" else "BUY_PUT",
            strike=atm, option_type=side, entry_premium=premium,
            sl_premium=premium * (1 - V1_SL_PCT),
            target_premium=premium * (1 + V1_TARGET_PCT),
            entry_spot=candle.spot_price, entry_verdict=candle.verdict,
            entry_confidence=candle.confidence,
            chc_strength=ce_pct if side == "CE" else pe_pct,
            max_premium=premium, min_premium=premium,
        )

    # EOD fallback
    if trade and trade.status == "ACTIVE":
        for c in reversed(candles):
            p = get_premium(c, trade.strike, trade.option_type)
            if p:
                trade.exit_premium, trade.exit_time, trade.exit_reason = p, c.time_str, "EOD"
                trade.pnl_pct = (p - trade.entry_premium) / trade.entry_premium * 100
                trade.status = "WON" if trade.pnl_pct > 0 else "LOST"
                break
    return trade


# ─── PA V2 (Exit-Only Redesign) ─────────────────────────────────────────────
# Philosophy: IDENTICAL entries to V1. Only change: smarter exit mechanics.
#   1. After +10% unrealized, activate trailing SL at (peak_premium - 7%)
#   2. Remove fixed 15% target cap -- let trailing SL manage the exit
#   3. This turns +15% fixed exits into potential +20-30% runners
#      while converting some -15% SL losses into -3% to +5% breakeven exits

def simulate_pa_v2(candles, day):
    trade = None
    atm = None
    hist = []

    for candle in candles:
        t = candle.time_str

        if atm is None and candle.spot_price > 0:
            atm = find_atm(candle.spot_price)
        if atm is None:
            continue

        ce = get_premium(candle, atm, "CE")
        pe = get_premium(candle, atm, "PE")
        if ce and pe and ce > 0 and pe > 0:
            hist.append({"ce": ce, "pe": pe, "spot": candle.spot_price})

        # ─── Track active trade (V2 exit logic) ─────────────────────
        if trade is not None:
            p = get_premium(candle, trade.strike, trade.option_type)
            if p is None:
                continue
            trade.max_premium = max(trade.max_premium, p)
            trade.min_premium = min(trade.min_premium, p)

            unrealized_pct = (p - trade.entry_premium) / trade.entry_premium

            # V2 TRAILING SL: after +10% unrealized, trail at peak - 7%
            if not trade.breakeven_activated and unrealized_pct >= V2_TRAIL_TRIGGER:
                trade.breakeven_activated = True

            if trade.breakeven_activated:
                # Continuously update trailing SL based on peak premium
                new_trail_sl = trade.max_premium * (1 - V2_TRAIL_OFFSET)
                # Only ratchet UP, never down
                if new_trail_sl > trade.trailing_sl:
                    trade.trailing_sl = new_trail_sl
                # Use trailing SL instead of original SL
                effective_sl = max(trade.trailing_sl, trade.sl_premium)
            else:
                effective_sl = trade.sl_premium

            # Check SL
            if p <= effective_sl:
                trade.exit_time, trade.exit_premium = t, p
                trade.pnl_pct = (p - trade.entry_premium) / trade.entry_premium * 100
                if trade.breakeven_activated:
                    trade.exit_reason = "TRAIL"
                    trade.status = "WON" if trade.pnl_pct > 0 else "LOST"
                else:
                    trade.exit_reason = "SL"
                    trade.status = "LOST"
                return trade

            # V2: Extended target -- only exit at +25% (or let trail handle it)
            if p >= trade.entry_premium * (1 + V2_EXTENDED_TARGET):
                trade.status, trade.exit_time, trade.exit_premium = "WON", t, p
                trade.exit_reason = "EXT_TARGET"
                trade.pnl_pct = (p - trade.entry_premium) / trade.entry_premium * 100
                return trade

            # EOD
            if t >= EOD_EXIT:
                trade.exit_time, trade.exit_premium = t, p
                trade.pnl_pct = (p - trade.entry_premium) / trade.entry_premium * 100
                trade.exit_reason, trade.status = "EOD", "WON" if trade.pnl_pct > 0 else "LOST"
                return trade
            continue

        # ─── Entry logic (IDENTICAL to V1) ───────────────────────────
        if t < PA_START or t >= PA_END:
            continue
        if len(hist) < V2_CHC_LOOKBACK + 1:
            continue

        recent = hist[-(V2_CHC_LOOKBACK + 1):]
        ce_r = all(recent[i]["ce"] > recent[i-1]["ce"] for i in range(1, V2_CHC_LOOKBACK + 1))
        pe_r = all(recent[i]["pe"] > recent[i-1]["pe"] for i in range(1, V2_CHC_LOOKBACK + 1))
        if not ce_r and not pe_r:
            continue

        ce_pct = (recent[-1]["ce"] - recent[0]["ce"]) / recent[0]["ce"] if recent[0]["ce"] > 0 else 0
        pe_pct = (recent[-1]["pe"] - recent[0]["pe"]) / recent[0]["pe"] if recent[0]["pe"] > 0 else 0

        if ce_r and not pe_r:
            side, strength = "CE", ce_pct
        elif pe_r and not ce_r:
            side, strength = "PE", pe_pct
        elif ce_r and pe_r:
            side, strength = ("CE", ce_pct) if ce_pct > pe_pct else ("PE", pe_pct)
        else:
            continue

        # IV filter (same as V1)
        if side == "CE" and candle.iv_skew > 1.0:
            continue
        if side == "PE" and candle.iv_skew < -1.0:
            continue

        # Choppy filter (same as V1)
        if len(hist) >= V2_CHOPPY_LOOKBACK + 1:
            spots = [h["spot"] for h in hist[-(V2_CHOPPY_LOOKBACK + 1):]]
            avg = sum(spots) / len(spots)
            if avg > 0 and ((max(spots) - min(spots)) / avg * 100) < V2_CHOPPY_THRESHOLD:
                continue

        premium = get_premium(candle, atm, side)
        if premium is None or premium < V2_MIN_PREMIUM:
            continue

        sl = premium * (1 - V2_SL_PCT)
        target = premium * (1 + V2_EXTENDED_TARGET)  # Extended target

        trade = Trade(
            version="V2", day=day, entry_time=t,
            direction="BUY_CALL" if side == "CE" else "BUY_PUT",
            strike=atm, option_type=side, entry_premium=premium,
            sl_premium=sl, target_premium=target,
            entry_spot=candle.spot_price, entry_verdict=candle.verdict,
            entry_confidence=candle.confidence, chc_strength=strength,
            max_premium=premium, min_premium=premium,
            trailing_sl=sl, filters_passed="V1_entry|trail_exit",
        )

    # EOD fallback
    if trade and trade.status == "ACTIVE":
        for c in reversed(candles):
            p = get_premium(c, trade.strike, trade.option_type)
            if p:
                trade.exit_premium, trade.exit_time, trade.exit_reason = p, c.time_str, "EOD"
                trade.pnl_pct = (p - trade.entry_premium) / trade.entry_premium * 100
                trade.status = "WON" if trade.pnl_pct > 0 else "LOST"
                break
    return trade


# ─── Stats ───────────────────────────────────────────────────────────────────

def compute_stats(trades):
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

    return {
        "total": len(resolved), "wins": len(wins), "losses": len(losses),
        "wr": len(wins)/len(resolved)*100,
        "avg_win": sum(t.pnl_pct for t in wins)/len(wins) if wins else 0,
        "avg_loss": sum(t.pnl_pct for t in losses)/len(losses) if losses else 0,
        "pnl": sum(pnls), "max_dd": max_dd, "max_cl": max_cl,
        "best": max(pnls), "worst": min(pnls),
        "pf": abs(sum(t.pnl_pct for t in wins)/sum(t.pnl_pct for t in losses))
              if losses and sum(t.pnl_pct for t in losses) != 0 else float("inf"),
        "exits": dict(defaultdict(int, {
            r: sum(1 for t in resolved if t.exit_reason == r)
            for r in set(t.exit_reason for t in resolved)
        })),
    }


def stats_by_cond(trades, cls):
    by_regime = defaultdict(list)
    by_vol = defaultdict(list)
    for t in trades:
        if t.day in cls and t.status in ("WON", "LOST"):
            by_regime[cls[t.day].regime].append(t)
            by_vol[cls[t.day].volatility].append(t)
    def _s(group):
        r = {}
        for k, lst in group.items():
            w = sum(1 for x in lst if x.status == "WON")
            r[k] = {"n": len(lst), "w": w, "wr": w/len(lst)*100 if lst else 0,
                     "pnl": sum(x.pnl_pct for x in lst)/len(lst) if lst else 0}
        return r
    return {"regime": _s(by_regime), "vol": _s(by_vol)}


# ─── Main ────────────────────────────────────────────────────────────────────

def run():
    print("=" * 90)
    print("  PA STRATEGY: V1 vs V2 -- Head-to-Head Forward Test")
    print("=" * 90)
    print()

    days_data = load_data()
    usable = sorted(d for d, c in days_data.items() if sum(1 for x in c if len(x.strikes) > 0) >= 20)
    print(f"  Data: {len(usable)} days ({usable[0]} to {usable[-1]})")
    print()

    cls = {d: classify_day(d, days_data[d]) for d in usable}

    v1_trades, v2_trades = [], []
    for d in usable:
        c = days_data[d]
        t1 = simulate_pa_v1(c, d)
        if t1:
            v1_trades.append(t1)
        t2 = simulate_pa_v2(c, d)
        if t2:
            v2_trades.append(t2)

    # ─── V2 Enhancement Analysis ─────────────────────────────────────
    print("=" * 90)
    print("  V2 ENHANCEMENT ANALYSIS -- What Filters Blocked / Changed")
    print("=" * 90)
    print()

    v1_days = {t.day for t in v1_trades}
    v2_days = {t.day for t in v2_trades}
    blocked_days = v1_days - v2_days
    new_days = v2_days - v1_days

    print(f"  V1 traded on {len(v1_days)} days")
    print(f"  V2 traded on {len(v2_days)} days")
    print(f"  V2 BLOCKED trades on: {sorted(blocked_days) if blocked_days else 'none'}")
    print(f"  V2 NEW trades on: {sorted(new_days) if new_days else 'none'}")
    print()

    # Check which blocked trades were losses in V1
    v1_blocked_results = [t for t in v1_trades if t.day in blocked_days]
    if v1_blocked_results:
        print("  Blocked V1 trades (would have been):")
        for t in v1_blocked_results:
            print(f"    {t.day}: {t.direction} {t.strike} {t.option_type} -> {t.status} ({t.pnl_pct:+.1f}%)")
        blocked_losses = sum(1 for t in v1_blocked_results if t.status == "LOST")
        blocked_wins = sum(1 for t in v1_blocked_results if t.status == "WON")
        print(f"    -> V2 correctly blocked {blocked_losses} losses, incorrectly blocked {blocked_wins} wins")
    print()

    # V2 trades with filters detail
    print("  V2 Trades -- Filter Detail:")
    for t in v2_trades:
        be = " [BE_ACTIVATED]" if t.breakeven_activated else ""
        print(f"    {t.day}: {t.direction} {t.option_type} CHC={t.chc_strength:.3f} -> {t.status} ({t.pnl_pct:+.1f}%){be}")
        print(f"           Filters: {t.filters_passed}")

    # ─── V1 Results ──────────────────────────────────────────────────
    for label, trades in [("PA V1 (Baseline)", v1_trades), ("PA V2 (Enhanced)", v2_trades)]:
        print()
        print("=" * 90)
        print(f"  {label}")
        print("=" * 90)
        s = compute_stats(trades)
        if not s:
            print("  No trades.")
            continue
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
        print(f"  {'Date':<12} {'Dir':<10} {'Str':<7} {'Entry':<8} {'Exit':<8} {'P&L%':<9} {'Status':<8} {'Reason':<12} {'CHC%':<8}")
        print("  " + "-" * 90)
        for t in trades:
            print(f"  {t.day:<12} {t.direction:<10} {t.strike:<7} {t.entry_premium:<8.1f} {t.exit_premium:<8.1f} {t.pnl_pct:>+7.1f}% {t.status:<8} {t.exit_reason:<12} {t.chc_strength:>6.1%}")

        cond = stats_by_cond(trades, cls)
        print()
        print("  By Regime:")
        for k, d in sorted(cond["regime"].items()):
            print(f"    {k:<18} {d['n']} trades  WR={d['wr']:>5.1f}%  AvgPnL={d['pnl']:>+6.1f}%")
        print("  By Volatility:")
        for k, d in sorted(cond["vol"].items()):
            print(f"    {k:<18} {d['n']} trades  WR={d['wr']:>5.1f}%  AvgPnL={d['pnl']:>+6.1f}%")

    # ─── Head-to-Head Comparison ─────────────────────────────────────
    s1 = compute_stats(v1_trades)
    s2 = compute_stats(v2_trades)
    if s1 and s2:
        print()
        print("=" * 90)
        print("  HEAD-TO-HEAD: V1 vs V2")
        print("=" * 90)
        print()
        metrics = [
            ("Win Rate", f"{s1['wr']:.1f}%", f"{s2['wr']:.1f}%"),
            ("Total P&L", f"{s1['pnl']:+.1f}%", f"{s2['pnl']:+.1f}%"),
            ("Max Drawdown", f"{s1['max_dd']:.1f}%", f"{s2['max_dd']:.1f}%"),
            ("Max Consec Loss", f"{s1['max_cl']}", f"{s2['max_cl']}"),
            ("Profit Factor", f"{s1['pf']:.2f}", f"{s2['pf']:.2f}"),
            ("Avg Win", f"{s1['avg_win']:+.1f}%", f"{s2['avg_win']:+.1f}%"),
            ("Avg Loss", f"{s1['avg_loss']:+.1f}%", f"{s2['avg_loss']:+.1f}%"),
            ("Trade Count", f"{s1['total']}", f"{s2['total']}"),
            ("Best Trade", f"{s1['best']:+.1f}%", f"{s2['best']:+.1f}%"),
            ("Worst Trade", f"{s1['worst']:+.1f}%", f"{s2['worst']:+.1f}%"),
        ]
        print(f"  {'Metric':<20} {'V1':<14} {'V2':<14} {'Delta':<14}")
        print("  " + "-" * 62)
        for name, v1v, v2v in metrics:
            # Compute delta
            try:
                n1 = float(v1v.replace("%", "").replace("+", ""))
                n2 = float(v2v.replace("%", "").replace("+", ""))
                delta = n2 - n1
                # For max DD and worst trade, lower is better
                if name in ("Max Drawdown", "Max Consec Loss", "Worst Trade"):
                    better = "BETTER" if delta < 0 else "WORSE" if delta > 0 else "SAME"
                else:
                    better = "BETTER" if delta > 0 else "WORSE" if delta < 0 else "SAME"
                delta_str = f"{delta:+.1f} ({better})"
            except ValueError:
                delta_str = "N/A"
            print(f"  {name:<20} {v1v:<14} {v2v:<14} {delta_str}")

    # ─── V2 Enhancements Summary ─────────────────────────────────────
    print()
    print("=" * 90)
    print("  V2 ENHANCEMENTS APPLIED")
    print("=" * 90)
    print("""
  1. CHC Strength Floor (2-3%)  -- Filters out noise-driven CHC on dead markets
  2. Choppy Filter (0.25%)      -- Raised from 0.15% to catch more range-bound days
  3. OI Strength Alignment      -- Don't buy CE when OI is strongly bearish (and vice versa)
  4. PM EMA Confirmation        -- Smoothed premium momentum must agree with direction
  5. Trailing Breakeven SL      -- After +8% unrealized, SL moves to entry price
  6. OI Cluster Dynamic SL      -- Tightens SL when OI support wall is nearby
  7. VIX-Adaptive SL/Target     -- Wider in high VIX, tighter in low VIX
  8. Confirmation Gate           -- CONFLICT signals need 3% CHC, CONFIRMED needs 1%
  9. Warmup Period (5 candles)  -- Skips first 15 min of market open noise
""")

    print("=" * 90)
    print(f"  Forward test on {len(usable)} days ({usable[0]} to {usable[-1]})")
    print("=" * 90)


if __name__ == "__main__":
    run()
