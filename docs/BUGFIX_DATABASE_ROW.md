# Bug Fix: AttributeError with sqlite3.Row

## Issue
```
AttributeError: 'sqlite3.Row' object has no attribute 'get'
```

## Root Cause
`sqlite3.Row` objects don't support the `.get()` method like Python dictionaries. The code was using `row.get("ce_volume", 0)` which fails.

## Fix Applied

**File**: `database.py`

**Changed from:**
```python
strikes[row["strike_price"]] = {
    "ce_volume": row.get("ce_volume", 0),  # ❌ Fails
    "pe_volume": row.get("pe_volume", 0),  # ❌ Fails
}
```

**Changed to:**
```python
# Handle volume columns that may not exist in old data
try:
    ce_volume = row["ce_volume"]
except (KeyError, IndexError):
    ce_volume = 0

try:
    pe_volume = row["pe_volume"]
except (KeyError, IndexError):
    pe_volume = 0

strikes[row["strike_price"]] = {
    "ce_volume": ce_volume,  # ✓ Works
    "pe_volume": pe_volume,  # ✓ Works
}
```

## Functions Fixed
1. `get_latest_snapshot()` (lines 180-199)
2. `get_strikes_for_timestamp()` (lines 264-280)

## Verification

Run test script:
```bash
uv run python test_db_fix.py
```

**Expected output:**
```
[OK] Database read successful
[OK] Strikes available: 97
[OK] Sample strike structure: ['ce_oi', 'ce_oi_change', 'ce_volume', 'pe_oi', 'pe_oi_change', 'pe_volume']
[OK] Volume fields present: ce_volume=0, pe_volume=0
```

## Next Steps

1. **Restart the app** - The error is now fixed
2. **Wait for next fetch** - New data will have actual volume values
3. **Check dashboard** - Volume metrics will display once fresh data arrives

## Why Volume Shows 0

Existing data was captured before volume extraction was implemented. The next 3-minute fetch cycle will populate volume fields with real data from NSE.

---

**Status**: ✓ Fixed
**Tested**: ✓ Verified
**Impact**: ✓ No data loss, backward compatible
