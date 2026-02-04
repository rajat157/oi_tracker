# Pattern Tracker Guide

## Overview

The Pattern Tracker monitors entry timing patterns to help identify optimal entry points for high RR setups. It runs automatically with each data fetch and logs patterns for later analysis.

## Quick Commands

### View Pattern Report
```bash
uv run python pattern_tracker.py --report
```

### View Simulation Report (with RR testing)
```bash
# Default: 7 days, 1 lot, 1:1 RR
uv run python scripts/simulate_quality_scoring.py --days 7

# Test with different RR ratios
uv run python scripts/simulate_quality_scoring.py --days 7 --rr 2
uv run python scripts/simulate_quality_scoring.py --days 7 --rr 3

# Test with multiple lots
uv run python scripts/simulate_quality_scoring.py --days 7 --lots 2
```

### Analyze High RR Patterns
```bash
uv run python scripts/analyze_high_rr_trades.py
```

### Analyze Entry Timing
```bash
uv run python scripts/analyze_entry_timing.py
```

### Find Common Patterns in High RR Setups
```bash
uv run python scripts/find_high_rr_patterns.py
```

### Analyze Quality Score 7+ Trades
```bash
uv run python scripts/analyze_quality7_trades.py
```

---

## What the Pattern Tracker Monitors

### 1. PM Reversal Patterns

Detects when Premium Momentum turns up from extreme negative levels:

| Pattern | Description |
|---------|-------------|
| `PM_REVERSAL_FROM_EXTREME` | PM turning up from < -50 |
| `PM_CROSSED_ABOVE_MINUS50` | PM crossing the -50 threshold |
| `PM_RECOVERING_TO_NEUTRAL` | PM going from < -30 to > -10 |

**Why this matters:** Our analysis showed that high RR setups often have negative PM at entry. Waiting for PM to start recovering may improve entry timing.

### 2. Failed Entry Recovery

Tracks what happens after a trade hits SL:

- Does price recover to entry level?
- Would it have hit the original target?
- How long does recovery take?

**Why this matters:** 83% of high RR setups hit SL before reaching target. Understanding recovery patterns helps evaluate if re-entry or wider SL strategies would work.

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `pm_history` | Rolling window of last 100 PM scores |
| `detected_patterns` | Logged PM reversal patterns |
| `failed_entry_tracking` | Trades that hit SL with recovery monitoring |

---

## Key Findings from Initial Analysis

Based on 6 unique high RR setups (3 days of data):

1. **Entry Timing Problem**
   - 83% of high RR trades hit SL first, then recovered
   - Average drawdown before recovery: 49.8%
   - We're entering TOO EARLY

2. **PM at Entry**
   - Original entry avg PM: -36.2
   - Optimal entry avg PM: -59.7 (MORE negative)
   - The best entries happened when PM was at its most negative (exhaustion)

3. **What to Watch For**
   - PM turning up from extreme negative = potential entry
   - Wait for selling exhaustion before entering
   - Consider delayed entry after initial signal

---

## Recommended Workflow

1. **Daily**: Let the tracker run automatically during market hours

2. **Weekly**: Run the report to check accumulated patterns
   ```bash
   uv run python pattern_tracker.py --report
   ```

3. **After 1-2 weeks**: Run full analysis to see if patterns are predictive
   ```bash
   uv run python scripts/analyze_entry_timing.py
   ```

---

## Interpreting the Report

### PM History Section
Shows recent PM values. Look for:
- Extreme negative values (< -50) followed by recovery
- Patterns in PM movement

### Detected Patterns Section
Shows each time a PM reversal was detected. Track:
- How many patterns detected
- What happened after each pattern

### Failed Entry Recovery Section
Shows trades that hit SL. Check:
- `Recovered` column: Did price return to entry?
- `Hit Target` column: Would target have been hit?
- High recovery % = consider re-entry strategy

### Summary Statistics
Quick overview of:
- Total patterns by type
- Recovery rate for failed entries
- Target hit rate after SL

---

## Future Improvements

Once we have 2+ weeks of data:

1. **Validate PM Reversal as Entry Signal**
   - Do entries after PM reversal have higher win rate?
   - What's the optimal PM threshold for entry?

2. **Optimize Entry Timing**
   - Should we delay entry by X minutes after signal?
   - Does waiting for "higher low" improve results?

3. **Re-entry Strategy**
   - If SL is hit and price recovers, should we re-enter?
   - What conditions make re-entry successful?
