# Quick Start: Volume-Weighted OI Analysis

## What's New?

🎯 **Volume-based conviction scoring** now weights OI changes by trading volume:
- **Fresh positions** (high volume) get 1.5x weight
- **Moderate positions** (medium volume) get 1.0x weight
- **Stale positions** (low volume) get 0.5x weight

This prevents false signals from old, inactive positions!

## Start the Application

```bash
cd D:\Projects\oi_tracker
uv run python app.py
```

**Expected Console Output:**
```
Added volume columns to oi_snapshots table
```
(Only shows on first run - this is the automatic database migration)

## View the Dashboard

Open http://localhost:5000 in your browser

**New Metrics Visible:**
1. **Volume PCR** - Put/Call ratio based on volume (not just OI)
2. **Avg Conviction** - Average conviction score (color-coded)
3. **Volume columns** in OTM/ITM strike tables
4. **Conviction multipliers** per strike (1.5x, 1.0x, 0.5x)

## Test Volume Weighting

After the app fetches data (wait ~3 minutes), run:

```bash
uv run python test_volume_weighting.py
```

**This will show:**
- ✅ Conviction multiplier formula validation
- ✅ Before/After comparison (unweighted vs weighted)
- ✅ Per-strike breakdown with volume and conviction

## What to Expect

### Key Metrics Card (Top Left)
```
┌─────────────────────────┐
│ Spot Price:    25,432   │
│ ATM Strike:    25,450   │
│ Expiry:        30-Jan   │
│ PCR:           1.15     │
│ Momentum:      ↑ +0.3%  │
│ Volume PCR:    0.92  ← NEW
│ Avg Conviction: 1.12x ← NEW
└─────────────────────────┘
```

### Strike Tables (Right Side)
```
OTM Calls (Bearish)
┌────────┬─────────┬──────────┬─────────┬────────────┐
│ Strike │   OI    │  Change  │ Volume  │ Conviction │← NEW
├────────┼─────────┼──────────┼─────────┼────────────┤
│ 25,500 │ 150,000 │  +10,000 │  8,000  │  0.80x     │
│ 25,550 │ 120,000 │  +15,000 │ 14,000  │  0.93x     │
│ 25,600 │  95,000 │  +20,000 │ 28,000  │  1.40x  ← High conviction!
└────────┴─────────┴──────────┴─────────┴────────────┘
```

**Color Coding:**
- 🟢 **Green (>1.2x)**: High conviction, fresh positions
- ⚪ **White (0.8-1.2x)**: Moderate conviction
- 🔴 **Red (<0.8x)**: Low conviction, stale positions

## Verification Steps

1. **Database Migration** ✅
   - Console shows "Added volume columns" message on first run
   - Or check manually:
     ```bash
     sqlite3 oi_tracker.db ".schema oi_snapshots"
     ```
   - Should see: `ce_volume INTEGER DEFAULT 0, pe_volume INTEGER DEFAULT 0`

2. **Volume Data Collection** ✅
   - Wait 3 minutes for first fetch cycle
   - Dashboard should display volume values (not "--")
   - Check browser console for WebSocket updates

3. **Conviction Weighting** ✅
   - Strike tables show conviction multipliers
   - High-volume strikes have higher conviction scores
   - Low-volume strikes have lower conviction scores

4. **Test Suite** ✅
   ```bash
   uv run python test_volume_weighting.py
   ```
   - All conviction multiplier tests pass
   - Shows weighted vs unweighted comparison
   - Displays per-strike breakdown

## Troubleshooting

### Volume columns show "--"
- **Cause**: No data fetched yet or old data in DB
- **Fix**: Wait for next 3-minute fetch cycle

### Migration doesn't run
- **Cause**: Database already has volume columns
- **Fix**: This is normal! Migration only runs once

### Conviction always shows 1.0x
- **Cause**: NSE volume data unavailable or not being extracted
- **Fix**: Check console for NSE fetcher errors

### Test file fails with "No snapshot data"
- **Cause**: Database empty or no recent data
- **Fix**: Let app run for at least one fetch cycle (3 minutes)

## Understanding the Impact

**Example Scenario:**

**Without Volume Weighting:**
```
Strike A: OI Change +50,000, Volume 5,000 (10% turnover)
Strike B: OI Change +30,000, Volume 28,000 (93% turnover)

Analysis treats A as stronger (50k > 30k)
```

**With Volume Weighting:**
```
Strike A: +50,000 × 0.5 (low conviction) = 25,000 weighted
Strike B: +30,000 × 1.5 (high conviction) = 45,000 weighted

Analysis correctly identifies B as stronger! ✅
```

## Data Flow Diagram

```
Every 3 minutes:
  NSE → Fetch option chain (columns 1-22)
         - Extract volume (columns 3, 19) ← NEW
         ↓
  Database → Save snapshot with volume ← NEW
         ↓
  Analyzer → Calculate conviction multipliers ← NEW
           → Weight OI changes by conviction ← NEW
         ↓
  WebSocket → Push to dashboard
         ↓
  Dashboard → Display volume metrics ← NEW
            → Color-code conviction ← NEW
```

## Next Steps

1. ✅ Start the app → migration runs automatically
2. ⏳ Wait 3 minutes → first data with volume arrives
3. 📊 Open dashboard → see new volume metrics
4. 🧪 Run test suite → verify weighting works
5. 📈 Monitor for 1 week → validate improved accuracy

---

**Status**: ✅ Implementation Complete
**Ready**: ✅ Production Ready
**Breaking Changes**: ❌ None (fully backward compatible)
