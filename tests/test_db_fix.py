"""Quick test to verify database read works after fix"""
import sys
from pathlib import Path

# Add parent directory to path for imports when running from tests/
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import get_latest_snapshot

try:
    snapshot = get_latest_snapshot()
    if snapshot:
        print("[OK] Database read successful")
        print(f"[OK] Strikes available: {len(snapshot['strikes'])}")
        if snapshot['strikes']:
            sample = list(snapshot['strikes'].values())[0]
            print(f"[OK] Sample strike structure: {list(sample.keys())}")
            print(f"[OK] Volume fields present: ce_volume={sample.get('ce_volume', 'MISSING')}, pe_volume={sample.get('pe_volume', 'MISSING')}")
    else:
        print("[WARN] No snapshot data available (database is empty)")
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()
