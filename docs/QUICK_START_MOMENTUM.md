# Quick Start: Momentum Feature

## What Changed?

Added **price momentum** (9-minute price trend) to improve signal accuracy during trending markets.

## How to Run

```bash
# Same as before - just start the app
uv run python app.py
```

Open http://localhost:5000 in your browser.

## What You'll See

### New in Dashboard

1. **Key Metrics Card** - New "Momentum (9m)" metric
   - Shows: `↑ +0.40%` (rising), `↓ -0.30%` (falling), or `→ 0.00%` (flat)
   - Color: Green (bullish), Red (bearish), Gray (neutral)

2. **Verdict Breakdown** - New "Price Momentum" section
   - Shows momentum score and weight (0% or 20%)
   - Only visible when momentum is active (after 9+ minutes)

### How It Works

**Momentum Calculation:**
- Looks at last 9 minutes of price data (3 data points)
- Calculates: `(Current Price - Price 9m Ago) / Price 9m Ago × 100`
- Amplified 20x: 1% price change = 20 momentum points

**Weight Integration:**
- When active: 50% OTM, 20% ATM, 10% ITM, **20% Momentum**
- Combined score = weighted average of all zones
- Verdict determined by combined score

**Example:**
```
Price: 25,220 → 25,320 (+0.40%)
Momentum Score: +8.0
OI Score (bearish): -10.0

Before Fix:
Combined = -10 → Verdict: "Bears Winning" ❌

After Fix:
OTM: -10 × 50% = -5.0
ATM: -2 × 20% = -0.4
ITM: -1 × 10% = -0.1
Momentum: +8 × 20% = +1.6
Combined = -3.9 → Verdict: "Slightly Bearish" ✓ (more accurate)
```

## Testing

```bash
# Test momentum calculation with historical data
uv run python test_momentum_fix.py
```

Expected output:
- Momentum scores for recent price changes
- Comparison of old vs new verdicts
- Confirmation that scores improve during trends

## When Momentum Activates

- **First 9 minutes**: Momentum is 0% (not enough data)
- **After 9+ minutes**: Momentum becomes 20% (3+ data points available)
- **Automatic**: No configuration needed

## Troubleshooting

### Momentum shows 0%
- **Cause**: Not enough historical data (< 3 records)
- **Solution**: Wait 9+ minutes for data to accumulate

### Momentum not showing in UI
- **Cause**: Weight is 0% (no data)
- **Solution**: The breakdown section auto-hides when weight is 0%

### Scores don't match expectations
- **Debug**: Check momentum calculation in test script
- **Check**: Price history in database
```bash
uv run python -c "from database import get_recent_price_trend; print(get_recent_price_trend(9))"
```

## Benefits

✅ Prevents "Bears Winning" signals during clear rallies
✅ Confirms OI signals when price moves in same direction
✅ Contradicts OI signals when price moves opposite direction
✅ Transparent - user can see momentum contribution

## Limitations

⚠️ 9-minute lag before activation
⚠️ Can't distinguish call/put buyers from writers (root cause remains)
⚠️ Conservative 20% weight (may miss some divergences)
⚠️ Price-only (doesn't consider volume)

## Next Steps

1. **Short-term**: Monitor accuracy over 1 week
2. **Medium-term**: Adjust weights if needed (Phase 2)
3. **Long-term**: Add context filters (volatility, liquidity, time-of-day)

## Documentation

- **Full Details**: See `MOMENTUM_FIX.md`
- **Implementation**: See `IMPLEMENTATION_SUMMARY.md`
- **Testing**: Run `test_momentum_fix.py`

---

**Ready to use!** Just start the app and momentum will activate automatically after 9 minutes of data collection.
