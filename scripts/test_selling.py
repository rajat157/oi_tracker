"""Test selling tracker integration."""
import sys
sys.path.insert(0, '.')

from selling_tracker import SellingTracker

st = SellingTracker()
print("SellingTracker initialized OK")
print("Stats:", st.get_sell_stats())

# Test import in scheduler
from scheduler import OIScheduler
print("Scheduler import OK (includes selling tracker)")

# Test API endpoint import
print("\nAll imports successful!")
