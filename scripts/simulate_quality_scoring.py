"""
Quality Score Backtest Simulation

Evaluates how the quality-based trade selection system would have performed
compared to the current regime-based filtering on historical data.

Usage:
    uv run python scripts/simulate_quality_scoring.py --days 7
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, time
from pathlib import Path
from collections import defaultdict
from typing import Optional, List, Dict, Tuple

# Add parent directory to path for imports when running from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

# Database path relative to project root
DB_PATH = Path(__file__).parent.parent / "oi_tracker.db"

# Trading hours
TRADE_SETUP_START = time(9, 30)
TRADE_SETUP_END = time(15, 15)

# Quality score threshold for high-quality trades
MIN_QUALITY_SCORE = 7

# Cooldown between trades (in minutes)
COOLDOWN_MINUTES = 12

# Max high-quality trades per day
MAX_TRADES_PER_DAY = 3


def get_connection():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def calculate_quality_score(analysis: dict) -> int:
    """
    Calculate quality score for trade setup.
    Same logic as TradeTracker._calculate_quality_score()

    Scoring criteria (max 9 points):
    - CONFIRMED status: +2 points
    - Optimal confidence (60-85%): +2 points
    - Good confidence (50-60% or 85-95%): +1 point
    - Strong verdict ("Winning"): +1 point
    - ITM option: +1 point
    - Low risk (<=15%): +1 point
    - Premium momentum aligned: +1 point
    """
    score = 0

    # 1. Confirmation status (+2 for CONFIRMED)
    if analysis.get("confirmation_status") == "CONFIRMED":
        score += 2

    # 2. Confidence range
    conf = analysis.get("signal_confidence", 0)
    if 60 <= conf <= 85:
        score += 2  # Optimal range
    elif 50 <= conf < 60 or 85 < conf <= 95:
        score += 1  # Good range

    # 3. Verdict strength
    verdict = analysis.get("verdict", "")
    if "Winning" in verdict:
        score += 1

    # 4. Trade setup details
    setup = analysis.get("trade_setup", {})
    if setup:
        # Moneyness - ITM options have higher delta
        if setup.get("moneyness") == "ITM":
            score += 1

        # Risk - tighter SL is better
        if setup.get("risk_pct", 20) <= 15:
            score += 1

    # 5. Premium momentum alignment
    pm = analysis.get("premium_momentum", {})
    pm_score = pm.get("premium_momentum_score", 0) if isinstance(pm, dict) else 0
    is_bullish = "bull" in verdict.lower()
    if (is_bullish and pm_score > 10) or (not is_bullish and pm_score < -10):
        score += 1

    return score


def count_confirmations(analysis: dict) -> int:
    """Count aligned confirmation signals."""
    confirmations = 0
    verdict = analysis.get("verdict", "").lower()
    is_bullish = "bull" in verdict

    # 1. OI-Price alignment
    if analysis.get("confirmation_status") == "CONFIRMED":
        confirmations += 1

    # 2. Market regime alignment
    regime = analysis.get("market_regime", {}).get("regime", "range_bound")
    if (is_bullish and regime == "trending_up") or \
       (not is_bullish and regime == "trending_down"):
        confirmations += 1

    # 3. Premium momentum alignment
    pm = analysis.get("premium_momentum", {})
    pm_score = pm.get("premium_momentum_score", 0)
    if (is_bullish and pm_score > 10) or (not is_bullish and pm_score < -10):
        confirmations += 1

    # 4. IV skew alignment
    iv_skew = analysis.get("iv_skew", {})
    skew_score = iv_skew.get("skew_score", 0) if isinstance(iv_skew, dict) else 0
    if (is_bullish and skew_score < -5) or (not is_bullish and skew_score > 5):
        confirmations += 1

    return confirmations


def would_pass_old_rules(analysis: dict) -> Tuple[bool, str]:
    """
    Check if trade passes strict regime filter (OLD rules).

    Returns:
        Tuple of (passes, rejection_reason)
    """
    verdict = analysis.get("verdict", "").lower()
    is_bullish = "bull" in verdict

    # Check confirmation status
    confirmation_status = analysis.get("confirmation_status", "")
    if confirmation_status not in ["CONFIRMED", "REVERSAL_ALERT"]:
        return False, f"Not confirmed ({confirmation_status})"

    # Check market regime
    market_regime = analysis.get("market_regime", {})
    regime = market_regime.get("regime", "range_bound")

    trade_setup = analysis.get("trade_setup", {})
    if not trade_setup:
        return False, "No trade setup"

    direction = trade_setup.get("direction", "")

    # OTM in range_bound is bad
    if regime == "range_bound" and trade_setup.get("moneyness") == "OTM":
        return False, "OTM in sideways market"

    # Strict regime alignment required
    if direction == "BUY_CALL" and regime != "trending_up":
        return False, f"CALL needs trending_up (got {regime})"
    elif direction == "BUY_PUT" and regime != "trending_down":
        return False, f"PUT needs trending_down (got {regime})"

    # Check confirmations (need 3 for old rules)
    confirmations = count_confirmations(analysis)
    if confirmations < 3:
        return False, f"Insufficient confirmations ({confirmations}/3)"

    return True, ""


def would_pass_new_rules(analysis: dict, quality_score: int) -> Tuple[bool, str]:
    """
    Check if trade passes with quality override (NEW rules).

    High quality trades (Score >= 7) can bypass range_bound regime.

    Returns:
        Tuple of (passes, rejection_reason)
    """
    verdict = analysis.get("verdict", "").lower()
    is_bullish = "bull" in verdict
    is_high_quality = quality_score >= MIN_QUALITY_SCORE

    # Check confirmation status
    confirmation_status = analysis.get("confirmation_status", "")
    if confirmation_status not in ["CONFIRMED", "REVERSAL_ALERT"]:
        return False, f"Not confirmed ({confirmation_status})"

    # Check market regime
    market_regime = analysis.get("market_regime", {})
    regime = market_regime.get("regime", "range_bound")

    trade_setup = analysis.get("trade_setup", {})
    if not trade_setup:
        return False, "No trade setup"

    direction = trade_setup.get("direction", "")

    # OTM in range_bound is still bad
    if regime == "range_bound" and trade_setup.get("moneyness") == "OTM":
        return False, "OTM in sideways market"

    # Wide SL in sideways is bad
    if regime == "range_bound" and trade_setup.get("risk_pct", 20) > 15:
        return False, f"SL too wide for sideways ({trade_setup.get('risk_pct')}%)"

    # Regime alignment with quality override
    if is_high_quality:
        # High quality: Only block if counter-trend
        if direction == "BUY_CALL" and regime == "trending_down":
            return False, "Counter-trend CALL"
        elif direction == "BUY_PUT" and regime == "trending_up":
            return False, "Counter-trend PUT"
        # range_bound is allowed for high-quality trades
    else:
        # Standard quality: Require strict regime alignment
        if direction == "BUY_CALL" and regime != "trending_up":
            return False, f"CALL needs trending_up or quality>=7 (got {regime}, score={quality_score})"
        elif direction == "BUY_PUT" and regime != "trending_down":
            return False, f"PUT needs trending_down or quality>=7 (got {regime}, score={quality_score})"

    # Check confirmations (reduced for high quality)
    required = 2 if is_high_quality else 3
    confirmations = count_confirmations(analysis)
    if confirmations < required:
        return False, f"Insufficient confirmations ({confirmations}/{required})"

    return True, ""


def get_historical_analyses(days: int = 7) -> List[dict]:
    """Fetch analysis_history records with parsed JSON."""
    conn = get_connection()
    cursor = conn.cursor()

    cutoff = datetime.now() - timedelta(days=days)

    cursor.execute("""
        SELECT id, timestamp, spot_price, verdict, signal_confidence, analysis_json
        FROM analysis_history
        WHERE timestamp >= ?
          AND analysis_json IS NOT NULL
        ORDER BY timestamp ASC
    """, (cutoff.isoformat(),))

    rows = cursor.fetchall()
    conn.close()

    analyses = []
    for row in rows:
        try:
            analysis_json = row["analysis_json"]
            if analysis_json:
                analysis = json.loads(analysis_json)
                analysis["_db_id"] = row["id"]
                analysis["_db_timestamp"] = row["timestamp"]
                analyses.append(analysis)
        except (json.JSONDecodeError, TypeError):
            continue

    return analyses


def get_trade_setups_for_validation(days: int = 7) -> Dict[str, dict]:
    """Get actual trade setups for validation."""
    conn = get_connection()
    cursor = conn.cursor()

    cutoff = datetime.now() - timedelta(days=days)

    cursor.execute("""
        SELECT *
        FROM trade_setups
        WHERE created_at >= ?
          AND status IN ('WON', 'LOST')
        ORDER BY created_at ASC
    """, (cutoff.isoformat(),))

    rows = cursor.fetchall()
    conn.close()

    # Index by timestamp (approximate match)
    setups = {}
    for row in rows:
        created_at = row["created_at"]
        setups[created_at] = dict(row)

    return setups


def get_snapshots_for_premium_tracking(start_time: str, end_time: str, strike: int) -> List[dict]:
    """Get OI snapshots for premium tracking."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT timestamp, ce_ltp, pe_ltp
        FROM oi_snapshots
        WHERE timestamp >= ? AND timestamp <= ?
          AND strike_price = ?
        ORDER BY timestamp ASC
    """, (start_time, end_time, strike))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def simulate_trade_outcome(analysis: dict, option_type: str, entry_premium: float,
                           sl_premium: float, target_premium: float) -> Tuple[str, float, str]:
    """
    Simulate trade outcome by looking ahead in price data.

    Returns:
        Tuple of (outcome: 'WON'/'LOST'/'EXPIRED', exit_premium, exit_reason)
    """
    trade_setup = analysis.get("trade_setup", {})
    if not trade_setup:
        return "EXPIRED", entry_premium, "No trade setup"

    strike = trade_setup.get("strike", 0)
    timestamp = analysis.get("_db_timestamp", "")

    if not strike or not timestamp:
        return "EXPIRED", entry_premium, "Missing data"

    # Look ahead 2 hours (or until end of day)
    try:
        start_dt = datetime.fromisoformat(timestamp)
    except ValueError:
        return "EXPIRED", entry_premium, "Invalid timestamp"

    end_dt = start_dt + timedelta(hours=2)

    # Get future snapshots
    snapshots = get_snapshots_for_premium_tracking(
        timestamp,
        end_dt.isoformat(),
        strike
    )

    if not snapshots:
        return "EXPIRED", entry_premium, "No future data"

    # Check each snapshot for SL/Target hits
    ltp_key = "ce_ltp" if option_type == "CE" else "pe_ltp"

    for snap in snapshots[1:]:  # Skip first (entry point)
        current_premium = snap.get(ltp_key, 0)
        if current_premium <= 0:
            continue

        # Check target hit
        if current_premium >= target_premium:
            return "WON", current_premium, "Target hit"

        # Check SL hit
        if current_premium <= sl_premium:
            return "LOST", current_premium, "SL hit"

    # Trade expired without hitting SL or target
    last_premium = snapshots[-1].get(ltp_key, entry_premium) if snapshots else entry_premium
    if last_premium > entry_premium:
        return "WON", last_premium, "Expired in profit"
    else:
        return "LOST", last_premium, "Expired in loss"


def run_simulation(days: int = 7) -> dict:
    """Main simulation loop with cooldown logic."""
    print(f"Loading historical analyses for last {days} days...")
    analyses = get_historical_analyses(days)
    print(f"Found {len(analyses)} analysis records with JSON data")

    if not analyses:
        print("No data to simulate!")
        return {}

    # Group by date
    daily_analyses = defaultdict(list)
    for a in analyses:
        ts = a.get("_db_timestamp", "")
        if ts:
            date = ts[:10]  # YYYY-MM-DD
            daily_analyses[date].append(a)

    # Results storage
    results = {
        "daily_summary": [],
        "quality_distribution": defaultdict(lambda: {"count": 0, "would_trade": 0, "wins": 0, "losses": 0, "pnl_sum": 0}),
        "old_rules_trades": [],
        "new_rules_trades": [],
        "high_quality_setups": [],
        "regime_override_trades": [],
    }

    # Simulation state
    last_trade_time = None

    print(f"\nSimulating across {len(daily_analyses)} trading days...")
    print("-" * 80)

    for date in sorted(daily_analyses.keys()):
        day_analyses = daily_analyses[date]
        day_stats = {
            "date": date,
            "candidates": 0,
            "high_quality": 0,
            "old_trades": 0,
            "new_trades": 0,
            "wins": 0,
            "losses": 0,
            "pnl_sum": 0,
        }

        trades_today = 0
        last_trade_time = None

        for analysis in day_analyses:
            ts = analysis.get("_db_timestamp", "")
            if not ts:
                continue

            # Check trading hours
            try:
                dt = datetime.fromisoformat(ts)
                current_time = dt.time()
                if current_time < TRADE_SETUP_START or current_time > TRADE_SETUP_END:
                    continue
            except ValueError:
                continue

            trade_setup = analysis.get("trade_setup", {})
            if not trade_setup:
                continue

            day_stats["candidates"] += 1

            # Calculate quality score
            quality_score = calculate_quality_score(analysis)
            is_high_quality = quality_score >= MIN_QUALITY_SCORE

            if is_high_quality:
                day_stats["high_quality"] += 1

            # Update quality distribution
            results["quality_distribution"][quality_score]["count"] += 1

            # Check old rules
            passes_old, old_reason = would_pass_old_rules(analysis)
            if passes_old:
                day_stats["old_trades"] += 1
                results["old_rules_trades"].append({
                    "timestamp": ts,
                    "quality_score": quality_score,
                    "verdict": analysis.get("verdict", ""),
                    "direction": trade_setup.get("direction", ""),
                })

            # Check new rules (with cooldown and daily limit)
            passes_new, new_reason = would_pass_new_rules(analysis, quality_score)

            if passes_new and is_high_quality:
                # Check cooldown
                if last_trade_time:
                    time_since_last = (dt - last_trade_time).total_seconds() / 60
                    if time_since_last < COOLDOWN_MINUTES:
                        continue

                # Check daily limit
                if trades_today >= MAX_TRADES_PER_DAY:
                    continue

                # This trade would be taken under new rules
                results["quality_distribution"][quality_score]["would_trade"] += 1

                # Simulate outcome
                entry_premium = trade_setup.get("entry_premium", 0)
                sl_premium = trade_setup.get("sl_premium", 0)
                target_premium = trade_setup.get("target1_premium", 0)
                option_type = trade_setup.get("option_type", "CE")

                outcome, exit_premium, exit_reason = simulate_trade_outcome(
                    analysis, option_type, entry_premium, sl_premium, target_premium
                )

                if entry_premium > 0:
                    pnl_pct = ((exit_premium - entry_premium) / entry_premium) * 100
                else:
                    pnl_pct = 0

                trade_result = {
                    "timestamp": ts,
                    "quality_score": quality_score,
                    "verdict": analysis.get("verdict", ""),
                    "direction": trade_setup.get("direction", ""),
                    "strike": trade_setup.get("strike", 0),
                    "entry_premium": entry_premium,
                    "exit_premium": exit_premium,
                    "outcome": outcome,
                    "exit_reason": exit_reason,
                    "pnl_pct": pnl_pct,
                    "regime": analysis.get("market_regime", {}).get("regime", "unknown"),
                }

                day_stats["new_trades"] += 1
                results["new_rules_trades"].append(trade_result)

                if outcome == "WON":
                    day_stats["wins"] += 1
                    results["quality_distribution"][quality_score]["wins"] += 1
                else:
                    day_stats["losses"] += 1
                    results["quality_distribution"][quality_score]["losses"] += 1

                day_stats["pnl_sum"] += pnl_pct
                results["quality_distribution"][quality_score]["pnl_sum"] += pnl_pct

                # Track high-quality setups
                results["high_quality_setups"].append(trade_result)

                # Track regime override trades
                regime = analysis.get("market_regime", {}).get("regime", "range_bound")
                if regime == "range_bound":
                    results["regime_override_trades"].append(trade_result)

                # Update state
                last_trade_time = dt
                trades_today += 1

        results["daily_summary"].append(day_stats)

        # Print daily progress
        print(f"  {date}: {day_stats['candidates']:3} candidates, "
              f"{day_stats['high_quality']:2} high-quality, "
              f"{day_stats['old_trades']:2} old-trades, "
              f"{day_stats['new_trades']:2} new-trades, "
              f"W:{day_stats['wins']} L:{day_stats['losses']}, "
              f"P&L: {day_stats['pnl_sum']:+.1f}%")

    return results


def generate_report(results: dict, days: int):
    """Print detailed comparison report."""
    if not results:
        print("No results to report!")
        return

    print("\n")
    print("=" * 80)
    print("QUALITY SCORE BACKTEST SIMULATION")

    # Calculate date range
    daily = results.get("daily_summary", [])
    if daily:
        start_date = daily[0]["date"]
        end_date = daily[-1]["date"]
        print(f"Data Range: {start_date} to {end_date} ({len(daily)} days)")
    print("=" * 80)

    # 1. Daily Summary Table
    print("\n" + "-" * 80)
    print("DAILY SUMMARY")
    print("-" * 80)
    print(f"{'Date':<12} | {'Candidates':>10} | {'Score>=7':>8} | {'OLD Trades':>10} | "
          f"{'NEW Trades':>10} | {'Wins':>4} | {'Losses':>6} | {'P&L %':>8}")
    print("-" * 80)

    total_old = 0
    total_new = 0
    total_wins = 0
    total_losses = 0
    total_pnl = 0

    for day in daily:
        print(f"{day['date']:<12} | {day['candidates']:>10} | {day['high_quality']:>8} | "
              f"{day['old_trades']:>10} | {day['new_trades']:>10} | "
              f"{day['wins']:>4} | {day['losses']:>6} | {day['pnl_sum']:>+8.1f}%")
        total_old += day['old_trades']
        total_new += day['new_trades']
        total_wins += day['wins']
        total_losses += day['losses']
        total_pnl += day['pnl_sum']

    print("-" * 80)
    total_candidates = sum(d['candidates'] for d in daily)
    total_hq = sum(d['high_quality'] for d in daily)
    print(f"{'TOTAL':<12} | {total_candidates:>10} | {total_hq:>8} | "
          f"{total_old:>10} | {total_new:>10} | "
          f"{total_wins:>4} | {total_losses:>6} | {total_pnl:>+8.1f}%")

    # 2. Comparison: Old vs New Rules
    print("\n" + "-" * 80)
    print("COMPARISON: OLD vs NEW RULES")
    print("-" * 80)

    old_trades = results.get("old_rules_trades", [])
    new_trades = results.get("new_rules_trades", [])

    new_wins = sum(1 for t in new_trades if t["outcome"] == "WON")
    new_losses = sum(1 for t in new_trades if t["outcome"] != "WON")
    new_win_rate = (new_wins / len(new_trades) * 100) if new_trades else 0
    new_total_pnl = sum(t["pnl_pct"] for t in new_trades)
    new_avg_pnl = (new_total_pnl / len(new_trades)) if new_trades else 0

    print(f"{'Metric':<25} | {'Old (Strict Regime)':>20} | {'New (Quality Override)':>22}")
    print("-" * 80)
    print(f"{'Total Trades':<25} | {len(old_trades):>20} | {len(new_trades):>22}")
    print(f"{'Win Rate':<25} | {'N/A (no simulation)':>20} | {new_win_rate:>21.1f}%")
    print(f"{'Total P&L':<25} | {'N/A':>20} | {new_total_pnl:>+21.1f}%")
    print(f"{'Avg P&L per Trade':<25} | {'N/A':>20} | {new_avg_pnl:>+21.1f}%")

    # 3. Quality Score Distribution
    print("\n" + "-" * 80)
    print("QUALITY SCORE DISTRIBUTION")
    print("-" * 80)
    print(f"{'Score':>5} | {'Count':>8} | {'Would Trade':>11} | {'Win Rate':>10} | {'Avg P&L':>10}")
    print("-" * 80)

    quality_dist = results.get("quality_distribution", {})
    for score in sorted(quality_dist.keys(), reverse=True):
        data = quality_dist[score]
        count = data["count"]
        would_trade = data["would_trade"]
        wins = data["wins"]
        losses = data["losses"]
        pnl_sum = data["pnl_sum"]

        total_trades = wins + losses
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        avg_pnl = (pnl_sum / total_trades) if total_trades > 0 else 0

        win_rate_str = f"{win_rate:.0f}%" if total_trades > 0 else "N/A"
        avg_pnl_str = f"{avg_pnl:+.1f}%" if total_trades > 0 else "N/A"

        print(f"{score:>5} | {count:>8} | {would_trade:>11} | {win_rate_str:>10} | {avg_pnl_str:>10}")

    # 4. High-Quality Trade Analysis
    print("\n" + "-" * 80)
    print("HIGH-QUALITY TRADE ANALYSIS (Score >= 7)")
    print("-" * 80)

    hq_setups = results.get("high_quality_setups", [])
    if hq_setups:
        for trade in hq_setups[:15]:  # Show first 15
            status = "WON" if trade["outcome"] == "WON" else "LOST"
            print(f"  [{status:>4}] {trade['timestamp'][11:19]} | "
                  f"Score: {trade['quality_score']} | "
                  f"{trade['direction']:<10} | "
                  f"Strike: {trade['strike']} | "
                  f"P&L: {trade['pnl_pct']:+.1f}% | "
                  f"{trade['exit_reason']}")

        if len(hq_setups) > 15:
            print(f"  ... and {len(hq_setups) - 15} more trades")

        hq_wins = sum(1 for t in hq_setups if t["outcome"] == "WON")
        hq_total_pnl = sum(t["pnl_pct"] for t in hq_setups)
        hq_win_rate = (hq_wins / len(hq_setups) * 100) if hq_setups else 0
        hq_avg_pnl = (hq_total_pnl / len(hq_setups)) if hq_setups else 0

        print(f"\n  Summary: {len(hq_setups)} trades, "
              f"Win Rate: {hq_win_rate:.1f}%, "
              f"Total P&L: {hq_total_pnl:+.1f}%, "
              f"Avg P&L: {hq_avg_pnl:+.1f}%")
    else:
        print("  No high-quality trades found in the simulation period")

    # 5. Regime Override Impact
    print("\n" + "-" * 80)
    print("REGIME OVERRIDE IMPACT")
    print("(Trades that would be blocked by old rules but allowed by new)")
    print("-" * 80)

    override_trades = results.get("regime_override_trades", [])
    if override_trades:
        for trade in override_trades[:10]:  # Show first 10
            status = "WON" if trade["outcome"] == "WON" else "LOST"
            print(f"  [{status:>4}] {trade['timestamp'][11:19]} | "
                  f"Score: {trade['quality_score']} | "
                  f"{trade['direction']:<10} | "
                  f"Regime: {trade['regime']:<12} | "
                  f"P&L: {trade['pnl_pct']:+.1f}%")

        if len(override_trades) > 10:
            print(f"  ... and {len(override_trades) - 10} more trades")

        override_wins = sum(1 for t in override_trades if t["outcome"] == "WON")
        override_total_pnl = sum(t["pnl_pct"] for t in override_trades)
        override_win_rate = (override_wins / len(override_trades) * 100) if override_trades else 0
        override_avg_pnl = (override_total_pnl / len(override_trades)) if override_trades else 0

        print(f"\n  Summary: {len(override_trades)} override trades, "
              f"Win Rate: {override_win_rate:.1f}%, "
              f"Total P&L: {override_total_pnl:+.1f}%, "
              f"Avg P&L: {override_avg_pnl:+.1f}%")
    else:
        print("  No regime override trades found")

    # Final Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"""
Key Findings:
- Total analysis records: {total_candidates}
- High-quality setups (Score >= 7): {total_hq} ({total_hq/total_candidates*100:.1f}% of all)
- Trades under OLD rules (strict regime): {len(old_trades)}
- Trades under NEW rules (quality override): {len(new_trades)}
- Win Rate (NEW rules): {new_win_rate:.1f}%
- Total P&L (NEW rules): {new_total_pnl:+.1f}%
- Avg P&L per Trade: {new_avg_pnl:+.1f}%

Regime Override Impact:
- {len(override_trades)} trades taken in range_bound markets
- Override Win Rate: {(sum(1 for t in override_trades if t['outcome'] == 'WON') / len(override_trades) * 100) if override_trades else 0:.1f}%
- Override Total P&L: {sum(t['pnl_pct'] for t in override_trades):+.1f}%
""")


def main():
    parser = argparse.ArgumentParser(
        description="Quality Score Backtest Simulation"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to simulate (default: 7)"
    )
    args = parser.parse_args()

    print("=" * 80)
    print("  QUALITY SCORE BACKTEST SIMULATION")
    print("=" * 80)
    print(f"\nSimulation Parameters:")
    print(f"  - Days to simulate: {args.days}")
    print(f"  - Min quality score for trade: {MIN_QUALITY_SCORE}")
    print(f"  - Cooldown between trades: {COOLDOWN_MINUTES} minutes")
    print(f"  - Max trades per day: {MAX_TRADES_PER_DAY}")
    print()

    # Run simulation
    results = run_simulation(days=args.days)

    # Generate report
    generate_report(results, days=args.days)


if __name__ == "__main__":
    main()
