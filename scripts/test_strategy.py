"""
Test script for the new OI Tracker trading strategy (85.7% Win Rate)

Strategy Rules:
- Time Window: 11:00 - 14:00 IST only
- Verdict: "Slightly Bullish" OR "Slightly Bearish" only
- Confidence: >= 65%
- ONE trade per day
- SL: -20%, Target: +22%
"""

from datetime import datetime, time
from unittest.mock import patch, MagicMock
import sys

# Fix encoding for Windows console
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Add project to path
sys.path.insert(0, '.')

from trade_tracker import (
    TradeTracker, 
    STRATEGY_TIME_START, 
    STRATEGY_TIME_END,
    STRATEGY_SL_PCT,
    STRATEGY_TARGET_PCT,
    STRATEGY_MIN_CONFIDENCE
)

# Use ASCII for test output
PASS = "[PASS]"
FAIL = "[FAIL]"


def test_time_window():
    """Test that trades are only allowed during 11:00-14:00."""
    print("\n=== Testing Time Window ===")
    
    tracker = TradeTracker()
    
    # Mock analysis with valid signal
    valid_analysis = {
        "verdict": "Slightly Bullish",
        "signal_confidence": 70,
        "spot_price": 25000,
        "trade_setup": {
            "direction": "BUY_CALL",
            "strike": 25000,
            "option_type": "CE",
            "entry_premium": 100
        }
    }
    
    test_times = [
        (time(9, 30), False, "Before window (9:30)"),
        (time(10, 59), False, "Just before window (10:59)"),
        (time(11, 0), True, "Start of window (11:00)"),
        (time(12, 30), True, "Middle of window (12:30)"),
        (time(14, 0), True, "End of window (14:00)"),
        (time(14, 1), False, "Just after window (14:01)"),
        (time(15, 0), False, "After window (15:00)"),
    ]
    
    for test_time, expected, description in test_times:
        with patch('trade_tracker.datetime') as mock_datetime:
            mock_now = MagicMock()
            mock_now.time.return_value = test_time
            mock_datetime.now.return_value = mock_now
            
            result = tracker._is_valid_strategy_signal(valid_analysis)
            status = PASS if result == expected else FAIL
            print(f"  {status} {description}: {'VALID' if result else 'INVALID'} (expected: {'VALID' if expected else 'INVALID'})")


def test_verdict_filter():
    """Test that only 'Slightly' verdicts are allowed."""
    print("\n=== Testing Verdict Filter ===")
    
    tracker = TradeTracker()
    
    test_verdicts = [
        ("Slightly Bullish", True),
        ("Slightly Bearish", True),
        ("Bulls Winning", False),
        ("Bears Winning", False),
        ("Neutral", False),
        ("Strongly Bullish", False),
        ("Strongly Bearish", False),
    ]
    
    for verdict, expected in test_verdicts:
        analysis = {
            "verdict": verdict,
            "signal_confidence": 70,
        }
        
        # Mock time to be within window
        with patch('trade_tracker.datetime') as mock_datetime:
            mock_now = MagicMock()
            mock_now.time.return_value = time(12, 0)
            mock_datetime.now.return_value = mock_now
            
            result = tracker._is_valid_strategy_signal(analysis)
            status = PASS if result == expected else FAIL
            print(f"  {status} '{verdict}': {'VALID' if result else 'INVALID'} (expected: {'VALID' if expected else 'INVALID'})")


def test_confidence_filter():
    """Test that confidence must be >= 65%."""
    print("\n=== Testing Confidence Filter ===")
    
    tracker = TradeTracker()
    
    test_confidences = [
        (50, False),
        (60, False),
        (64, False),
        (65, True),
        (70, True),
        (85, True),
        (95, True),
    ]
    
    for confidence, expected in test_confidences:
        analysis = {
            "verdict": "Slightly Bullish",
            "signal_confidence": confidence,
        }
        
        # Mock time to be within window
        with patch('trade_tracker.datetime') as mock_datetime:
            mock_now = MagicMock()
            mock_now.time.return_value = time(12, 0)
            mock_datetime.now.return_value = mock_now
            
            result = tracker._is_valid_strategy_signal(analysis)
            status = PASS if result == expected else FAIL
            print(f"  {status} Confidence {confidence}%: {'VALID' if result else 'INVALID'} (expected: {'VALID' if expected else 'INVALID'})")


def test_one_trade_per_day():
    """Test that only one trade per day is allowed."""
    print("\n=== Testing One Trade Per Day ===")
    
    tracker = TradeTracker()
    
    # Test with no trades today
    with patch('trade_tracker.get_todays_trades') as mock_get_trades:
        mock_get_trades.return_value = []
        result = tracker._already_traded_today()
        status = PASS if result == False else FAIL
        print(f"  {status} No trades today: {'BLOCKED' if result else 'ALLOWED'} (expected: ALLOWED)")
    
    # Test with one trade today
    with patch('trade_tracker.get_todays_trades') as mock_get_trades:
        mock_get_trades.return_value = [{"id": 1, "direction": "BUY_CALL"}]
        result = tracker._already_traded_today()
        status = PASS if result == True else FAIL
        print(f"  {status} One trade today: {'BLOCKED' if result else 'ALLOWED'} (expected: BLOCKED)")
    
    # Test with multiple trades today
    with patch('trade_tracker.get_todays_trades') as mock_get_trades:
        mock_get_trades.return_value = [
            {"id": 1, "direction": "BUY_CALL"},
            {"id": 2, "direction": "BUY_PUT"}
        ]
        result = tracker._already_traded_today()
        status = PASS if result == True else FAIL
        print(f"  {status} Multiple trades today: {'BLOCKED' if result else 'ALLOWED'} (expected: BLOCKED)")


def test_sl_target_calculation():
    """Test that SL and Target are calculated correctly."""
    print("\n=== Testing SL/Target Calculation ===")
    
    entry_prices = [100, 150, 200, 171.50]
    
    for entry in entry_prices:
        sl = round(entry * (1 - STRATEGY_SL_PCT / 100), 2)
        target = round(entry * (1 + STRATEGY_TARGET_PCT / 100), 2)
        
        expected_sl = round(entry * 0.80, 2)  # -20%
        expected_target = round(entry * 1.22, 2)  # +22%
        
        sl_ok = abs(sl - expected_sl) < 0.01
        target_ok = abs(target - expected_target) < 0.01
        
        status = PASS if (sl_ok and target_ok) else FAIL
        print(f"  {status} Entry Rs.{entry:.2f}: SL=Rs.{sl:.2f} (-20%), Target=Rs.{target:.2f} (+22%)")


def test_direction_lock():
    """Test that cancel_on_direction_change no longer cancels setups."""
    print("\n=== Testing Direction Lock (No Cancellation) ===")
    
    tracker = TradeTracker()
    
    # Mock an active PENDING setup
    with patch('trade_tracker.get_active_trade_setup') as mock_get_setup:
        mock_get_setup.return_value = {
            "id": 1,
            "status": "PENDING",
            "direction": "BUY_CALL"
        }
        
        # Try to cancel with opposite verdict
        result = tracker.cancel_on_direction_change("Slightly Bearish", datetime.now())
        status = PASS if result == False else FAIL
        print(f"  {status} Direction flip BUY_CALL -> Bearish: {'CANCELLED' if result else 'LOCKED'} (expected: LOCKED)")


def test_constants():
    """Verify strategy constants are set correctly."""
    print("\n=== Verifying Strategy Constants ===")
    
    checks = [
        (STRATEGY_TIME_START, time(11, 0), "Time Start"),
        (STRATEGY_TIME_END, time(14, 0), "Time End"),
        (STRATEGY_SL_PCT, 20.0, "SL Percentage"),
        (STRATEGY_TARGET_PCT, 22.0, "Target Percentage"),
        (STRATEGY_MIN_CONFIDENCE, 65.0, "Min Confidence"),
    ]
    
    for actual, expected, name in checks:
        status = PASS if actual == expected else FAIL
        print(f"  {status} {name}: {actual} (expected: {expected})")


def main():
    print("=" * 60)
    print("OI TRACKER - NEW STRATEGY VALIDATION")
    print("85.7% Win Rate Backtest Strategy")
    print("=" * 60)
    
    test_constants()
    test_time_window()
    test_verdict_filter()
    test_confidence_filter()
    test_one_trade_per_day()
    test_sl_target_calculation()
    test_direction_lock()
    
    print("\n" + "=" * 60)
    print("ALL TESTS COMPLETED")
    print("=" * 60)


if __name__ == "__main__":
    main()
