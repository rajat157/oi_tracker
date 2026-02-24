"""
Compare Fetchers — Side-by-side validation of Kite vs NSE data.

Run during market hours to verify Kite output matches NSE.
Usage: uv run python scripts/compare_fetchers.py

Tolerances:
  - Spot price: ±0.5 points
  - OI per strike: ±5%
  - LTP per strike: ±Rs 1
  - IV per strike: ±2%
  - VIX: ±0.5
  - Futures OI: ±1%
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from nse_fetcher import NSEFetcher
from kite_data import KiteDataFetcher


def compare(nse_data, kite_data):
    """Compare two parsed option chain datasets."""

    print("=" * 70)
    print("  FETCHER COMPARISON: NSE (Selenium) vs Kite (REST API)")
    print("=" * 70)

    # --- Spot price ---
    nse_spot = nse_data["spot_price"]
    kite_spot = kite_data["spot_price"]
    spot_diff = abs(nse_spot - kite_spot)
    spot_ok = spot_diff <= 0.5
    print(f"\n{'SPOT PRICE':30s}  NSE: {nse_spot:>10.2f}  Kite: {kite_spot:>10.2f}  "
          f"Diff: {spot_diff:>6.2f}  {'OK' if spot_ok else 'MISMATCH'}")

    # --- Expiry ---
    nse_exp = nse_data.get("current_expiry", "?")
    kite_exp = kite_data.get("current_expiry", "?")
    print(f"{'EXPIRY':30s}  NSE: {nse_exp:>10s}  Kite: {kite_exp:>10s}")

    # --- Strikes comparison ---
    nse_strikes = set(nse_data["strikes"].keys())
    kite_strikes = set(kite_data["strikes"].keys())
    common = sorted(nse_strikes & kite_strikes)
    only_nse = sorted(nse_strikes - kite_strikes)
    only_kite = sorted(kite_strikes - nse_strikes)

    print(f"\n{'STRIKES':30s}  NSE: {len(nse_strikes):>4d}  Kite: {len(kite_strikes):>4d}  "
          f"Common: {len(common)}  NSE-only: {len(only_nse)}  Kite-only: {len(only_kite)}")

    # --- Per-strike comparison (near ATM) ---
    atm = min(common, key=lambda s: abs(s - kite_spot)) if common else 0
    nearby = [s for s in common if abs(s - atm) <= 250]  # ±5 strikes

    print(f"\n{'STRIKE':>8s} | {'CE OI':>10s}  {'CE OI Δ':>10s}  {'CE LTP':>8s}  {'CE IV':>6s} | "
          f"{'PE OI':>10s}  {'PE OI Δ':>10s}  {'PE LTP':>8s}  {'PE IV':>6s}")
    print("-" * 110)

    oi_diffs = []
    ltp_diffs = []
    iv_diffs = []

    for strike in nearby:
        nd = nse_data["strikes"][strike]
        kd = kite_data["strikes"][strike]

        # Print NSE row
        print(f"{strike:>8d} | "
              f"{nd['ce_oi']:>10,d}  {nd['ce_oi_change']:>+10,d}  {nd['ce_ltp']:>8.2f}  {nd['ce_iv']:>6.2f} | "
              f"{nd['pe_oi']:>10,d}  {nd['pe_oi_change']:>+10,d}  {nd['pe_ltp']:>8.2f}  {nd['pe_iv']:>6.2f}  [NSE]")

        # Print Kite row
        print(f"{'':>8s} | "
              f"{kd['ce_oi']:>10,d}  {kd['ce_oi_change']:>+10,d}  {kd['ce_ltp']:>8.2f}  {kd['ce_iv']:>6.2f} | "
              f"{kd['pe_oi']:>10,d}  {kd['pe_oi_change']:>+10,d}  {kd['pe_ltp']:>8.2f}  {kd['pe_iv']:>6.2f}  [KITE]")

        # Compute diffs
        for prefix in ("ce", "pe"):
            nse_oi = nd[f"{prefix}_oi"]
            kite_oi = kd[f"{prefix}_oi"]
            if nse_oi > 0:
                oi_diffs.append(abs(nse_oi - kite_oi) / nse_oi * 100)

            ltp_diffs.append(abs(nd[f"{prefix}_ltp"] - kd[f"{prefix}_ltp"]))
            iv_diffs.append(abs(nd[f"{prefix}_iv"] - kd[f"{prefix}_iv"]))

        print()

    # --- Summary ---
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)

    if oi_diffs:
        avg_oi = sum(oi_diffs) / len(oi_diffs)
        max_oi = max(oi_diffs)
        print(f"  OI difference:   avg {avg_oi:.1f}%  max {max_oi:.1f}%  "
              f"{'OK (< 5%)' if max_oi < 5 else 'HIGH'}")

    if ltp_diffs:
        avg_ltp = sum(ltp_diffs) / len(ltp_diffs)
        max_ltp = max(ltp_diffs)
        print(f"  LTP difference:  avg Rs {avg_ltp:.2f}  max Rs {max_ltp:.2f}  "
              f"{'OK (< Rs 1)' if max_ltp < 1 else 'HIGH (timing?)'}")

    if iv_diffs:
        avg_iv = sum(iv_diffs) / len(iv_diffs)
        max_iv = max(iv_diffs)
        print(f"  IV difference:   avg {avg_iv:.2f}%  max {max_iv:.2f}%  "
              f"{'OK (< 2%)' if max_iv < 2 else 'EXPECTED (different method)'}")

    print(f"  Spot difference: {spot_diff:.2f} pts  {'OK' if spot_ok else 'MISMATCH'}")
    print()


def main():
    print("Fetching from NSE (Selenium)...")
    nse_fetcher = NSEFetcher(headless=True)
    try:
        nse_raw = nse_fetcher.fetch_option_chain()
        if not nse_raw:
            print("ERROR: NSE fetch failed")
            return
        nse_data = nse_fetcher.parse_option_data(nse_raw)
        if not nse_data:
            print("ERROR: NSE parse failed")
            return
    finally:
        nse_fetcher.close()

    print("Fetching from Kite (REST API)...")
    kite_fetcher = KiteDataFetcher()
    kite_data = kite_fetcher.fetch_option_chain()
    if not kite_data:
        print("ERROR: Kite fetch failed")
        return

    # Fetch VIX from both
    print("\nFetching VIX...")
    nse_vix = nse_fetcher.fetch_india_vix()
    kite_vix = kite_fetcher.fetch_india_vix()
    if nse_vix and kite_vix:
        vix_diff = abs(nse_vix - kite_vix)
        print(f"  VIX — NSE: {nse_vix:.2f}  Kite: {kite_vix:.2f}  "
              f"Diff: {vix_diff:.2f}  {'OK' if vix_diff < 0.5 else 'MISMATCH'}")

    # Fetch futures from both
    print("\nFetching Futures...")
    nse_fut = nse_fetcher.fetch_futures_data()
    kite_fut = kite_fetcher.fetch_futures_data()
    if nse_fut and kite_fut:
        oi_diff = abs(nse_fut['future_oi'] - kite_fut['future_oi'])
        oi_pct = (oi_diff / nse_fut['future_oi'] * 100) if nse_fut['future_oi'] > 0 else 0
        print(f"  Futures OI — NSE: {nse_fut['future_oi']:,}  Kite: {kite_fut['future_oi']:,}  "
              f"Diff: {oi_pct:.1f}%  {'OK' if oi_pct < 1 else 'HIGH'}")
        print(f"  Futures Price — NSE: {nse_fut['future_price']:.2f}  Kite: {kite_fut['future_price']:.2f}")

    compare(nse_data, kite_data)


if __name__ == "__main__":
    main()
