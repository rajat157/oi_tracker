"""
Backtest: How would futures data integration affect signal quality?

This script:
1. Pulls historical data with futures OI
2. Simulates different futures integration approaches
3. Compares accuracy with and without futures data
4. Generates a detailed report
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

DB_PATH = Path(__file__).parent.parent / "oi_tracker.db"


def get_historical_data():
    """Pull all analysis history with futures data."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT timestamp, spot_price, verdict, signal_confidence,
               futures_oi, futures_oi_change, futures_basis, 
               call_oi_change, put_oi_change, vix, max_pain, analysis_json
        FROM analysis_history
        ORDER BY timestamp ASC
    ''')
    
    rows = cursor.fetchall()
    conn.close()
    
    # Parse combined_score from analysis_json
    results = []
    for row in rows:
        data = dict(row)
        if data.get('analysis_json'):
            try:
                analysis = json.loads(data['analysis_json'])
                data['combined_score'] = analysis.get('combined_score', 0)
            except:
                data['combined_score'] = 0
        else:
            data['combined_score'] = 0
        results.append(data)
    
    return results


def calculate_price_movement(data, lookahead_periods=5):
    """Calculate actual price movement for each data point."""
    results = []
    
    for i, row in enumerate(data[:-lookahead_periods]):
        current_price = row['spot_price']
        future_price = data[i + lookahead_periods]['spot_price']
        
        price_change = future_price - current_price
        price_change_pct = (price_change / current_price) * 100
        
        actual_direction = "bullish" if price_change > 0 else "bearish" if price_change < 0 else "flat"
        
        results.append({
            **row,
            'future_price': future_price,
            'price_change': price_change,
            'price_change_pct': price_change_pct,
            'actual_direction': actual_direction
        })
    
    return results


def extract_signal_direction(verdict):
    """Extract bullish/bearish from verdict string."""
    verdict_lower = verdict.lower()
    if "bull" in verdict_lower:
        return "bullish"
    elif "bear" in verdict_lower:
        return "bearish"
    return "neutral"


def simulate_futures_integration(row, weight=0.15):
    """
    Simulate what the combined score would be with futures integration.
    
    Approaches tested:
    1. Futures OI direction adjustment (±10-20 points)
    2. Futures basis adjustment (±5-15 points based on premium/discount)
    3. Combined approach
    """
    original_score = row['combined_score'] or 0
    futures_oi_change = row['futures_oi_change'] or 0
    futures_basis = row['futures_basis'] or 0
    
    # Approach 1: Futures OI Change
    # Rising futures OI = bullish (longs adding)
    # Falling futures OI = bearish (longs exiting or shorts adding)
    if futures_oi_change > 5000:  # Significant positive change
        futures_oi_adjustment = min(15, futures_oi_change / 10000 * 15)
    elif futures_oi_change < -5000:  # Significant negative change
        futures_oi_adjustment = max(-15, futures_oi_change / 10000 * 15)
    else:
        futures_oi_adjustment = 0
    
    # Approach 2: Futures Basis
    # Positive basis (futures > spot) = bullish sentiment
    # Negative basis = bearish sentiment
    if futures_basis > 10:  # Significant premium
        basis_adjustment = min(10, futures_basis / 5)
    elif futures_basis < -10:  # Significant discount
        basis_adjustment = max(-10, futures_basis / 5)
    else:
        basis_adjustment = 0
    
    # Approach 3: Combined
    combined_adjustment = (futures_oi_adjustment * 0.7) + (basis_adjustment * 0.3)
    
    return {
        'original_score': original_score,
        'futures_oi_adjustment': round(futures_oi_adjustment, 2),
        'basis_adjustment': round(basis_adjustment, 2),
        'combined_adjustment': round(combined_adjustment, 2),
        'new_score_oi_only': round(original_score + futures_oi_adjustment, 2),
        'new_score_basis_only': round(original_score + basis_adjustment, 2),
        'new_score_combined': round(original_score + combined_adjustment, 2)
    }


def evaluate_accuracy(data, score_key='combined_score'):
    """Calculate accuracy metrics for a given score."""
    correct = 0
    total = 0
    
    by_strength = defaultdict(lambda: {'correct': 0, 'total': 0})
    by_direction = defaultdict(lambda: {'correct': 0, 'total': 0})
    
    for row in data:
        score = row.get(score_key, row.get('combined_score', 0)) or 0
        actual = row['actual_direction']
        
        if actual == 'flat':
            continue
        
        # Determine predicted direction from score
        if score > 15:
            predicted = "bullish"
            strength = "strong" if score > 40 else "moderate"
        elif score < -15:
            predicted = "bearish"
            strength = "strong" if score < -40 else "moderate"
        else:
            predicted = "neutral"
            strength = "weak"
        
        if predicted == "neutral":
            continue
        
        total += 1
        is_correct = predicted == actual
        if is_correct:
            correct += 1
        
        by_strength[strength]['total'] += 1
        by_strength[strength]['correct'] += int(is_correct)
        
        by_direction[predicted]['total'] += 1
        by_direction[predicted]['correct'] += int(is_correct)
    
    accuracy = (correct / total * 100) if total > 0 else 0
    
    return {
        'accuracy': round(accuracy, 2),
        'correct': correct,
        'total': total,
        'by_strength': dict(by_strength),
        'by_direction': dict(by_direction)
    }


def run_backtest():
    """Run the complete backtest and generate report."""
    print("=" * 70)
    print("  FUTURES DATA INTEGRATION BACKTEST")
    print("=" * 70)
    
    # Get data
    data = get_historical_data()
    print(f"\nTotal records: {len(data)}")
    
    if len(data) < 10:
        print("Not enough data for meaningful backtest")
        return
    
    # Filter for records with futures data
    data_with_futures = [d for d in data if d.get('futures_oi_change') and d['futures_oi_change'] != 0]
    print(f"Records with futures OI data: {len(data_with_futures)}")
    
    if len(data_with_futures) < 10:
        print("Not enough futures data for backtest. Using all records.")
        data_to_use = data
    else:
        data_to_use = data_with_futures
    
    # Calculate price movements (15-min lookahead = 5 periods of 3 min)
    data_with_outcomes = calculate_price_movement(data_to_use, lookahead_periods=5)
    print(f"Records with outcomes: {len(data_with_outcomes)}")
    
    if len(data_with_outcomes) < 5:
        print("Not enough data with outcomes")
        return
    
    # Date range
    print(f"Date range: {data_with_outcomes[0]['timestamp'][:10]} to {data_with_outcomes[-1]['timestamp'][:10]}")
    
    # Simulate futures integration for each record
    for row in data_with_outcomes:
        simulation = simulate_futures_integration(row)
        row.update(simulation)
    
    # Evaluate different approaches
    print("\n" + "=" * 70)
    print("  ACCURACY COMPARISON")
    print("=" * 70)
    
    # Original (no futures in score)
    original_accuracy = evaluate_accuracy(data_with_outcomes, 'original_score')
    print(f"\n1. ORIGINAL (current implementation):")
    print(f"   Accuracy: {original_accuracy['accuracy']}% ({original_accuracy['correct']}/{original_accuracy['total']})")
    print(f"   By strength: {original_accuracy['by_strength']}")
    print(f"   By direction: {original_accuracy['by_direction']}")
    
    # With futures OI adjustment
    for row in data_with_outcomes:
        row['test_score'] = row['new_score_oi_only']
    oi_accuracy = evaluate_accuracy(data_with_outcomes, 'test_score')
    print(f"\n2. WITH FUTURES OI ADJUSTMENT (±15 pts):")
    print(f"   Accuracy: {oi_accuracy['accuracy']}% ({oi_accuracy['correct']}/{oi_accuracy['total']})")
    print(f"   By strength: {oi_accuracy['by_strength']}")
    print(f"   By direction: {oi_accuracy['by_direction']}")
    
    # With basis adjustment
    for row in data_with_outcomes:
        row['test_score'] = row['new_score_basis_only']
    basis_accuracy = evaluate_accuracy(data_with_outcomes, 'test_score')
    print(f"\n3. WITH FUTURES BASIS ADJUSTMENT (±10 pts):")
    print(f"   Accuracy: {basis_accuracy['accuracy']}% ({basis_accuracy['correct']}/{basis_accuracy['total']})")
    print(f"   By strength: {basis_accuracy['by_strength']}")
    print(f"   By direction: {basis_accuracy['by_direction']}")
    
    # With combined adjustment
    for row in data_with_outcomes:
        row['test_score'] = row['new_score_combined']
    combined_accuracy = evaluate_accuracy(data_with_outcomes, 'test_score')
    print(f"\n4. WITH COMBINED FUTURES ADJUSTMENT:")
    print(f"   Accuracy: {combined_accuracy['accuracy']}% ({combined_accuracy['correct']}/{combined_accuracy['total']})")
    print(f"   By strength: {combined_accuracy['by_strength']}")
    print(f"   By direction: {combined_accuracy['by_direction']}")
    
    # Futures alignment analysis
    print("\n" + "=" * 70)
    print("  FUTURES ALIGNMENT ANALYSIS")
    print("=" * 70)
    
    futures_aligned = 0
    futures_conflicted = 0
    aligned_correct = 0
    conflicted_correct = 0
    
    for row in data_with_outcomes:
        futures_oi = row.get('futures_oi_change', 0) or 0
        original_score = row.get('original_score', 0) or 0
        actual = row['actual_direction']
        
        if actual == 'flat':
            continue
        
        futures_direction = "bullish" if futures_oi > 0 else "bearish" if futures_oi < 0 else "neutral"
        options_direction = "bullish" if original_score > 15 else "bearish" if original_score < -15 else "neutral"
        
        if futures_direction == "neutral" or options_direction == "neutral":
            continue
        
        is_aligned = futures_direction == options_direction
        is_correct = options_direction == actual
        
        if is_aligned:
            futures_aligned += 1
            if is_correct:
                aligned_correct += 1
        else:
            futures_conflicted += 1
            if is_correct:
                conflicted_correct += 1
    
    print(f"\nWhen Futures and Options ALIGNED:")
    if futures_aligned > 0:
        print(f"   Count: {futures_aligned}")
        print(f"   Accuracy: {aligned_correct/futures_aligned*100:.1f}%")
    else:
        print("   No aligned signals found")
    
    print(f"\nWhen Futures and Options CONFLICTED:")
    if futures_conflicted > 0:
        print(f"   Count: {futures_conflicted}")
        print(f"   Accuracy: {conflicted_correct/futures_conflicted*100:.1f}%")
    else:
        print("   No conflicted signals found")
    
    # Summary
    print("\n" + "=" * 70)
    print("  SUMMARY & RECOMMENDATIONS")
    print("=" * 70)
    
    improvements = {
        'Futures OI': oi_accuracy['accuracy'] - original_accuracy['accuracy'],
        'Futures Basis': basis_accuracy['accuracy'] - original_accuracy['accuracy'],
        'Combined': combined_accuracy['accuracy'] - original_accuracy['accuracy']
    }
    
    best_approach = max(improvements, key=improvements.get)
    best_improvement = improvements[best_approach]
    
    print(f"\nOriginal Accuracy: {original_accuracy['accuracy']}%")
    print(f"\nImpact of each approach:")
    for approach, improvement in improvements.items():
        sign = "+" if improvement >= 0 else ""
        print(f"   {approach}: {sign}{improvement:.2f}%")
    
    print(f"\nBest approach: {best_approach} ({'+' if best_improvement >= 0 else ''}{best_improvement:.2f}%)")
    
    if best_improvement > 2:
        print(f"\n[OK] RECOMMENDATION: Implement {best_approach} adjustment")
    elif best_improvement > 0:
        print(f"\n[MAYBE] MARGINAL IMPROVEMENT: {best_approach} helps slightly")
    else:
        print(f"\n[NO] Futures adjustment to score doesn't help")
    
    # KEY INSIGHT
    print("\n" + "=" * 70)
    print("  KEY INSIGHT: USE FUTURES AS A FILTER, NOT SCORE ADJUSTMENT")
    print("=" * 70)
    if futures_aligned > 0 and futures_conflicted > 0:
        aligned_acc = aligned_correct / futures_aligned * 100
        conflict_acc = conflicted_correct / futures_conflicted * 100
        improvement = aligned_acc - original_accuracy['accuracy']
        print(f"\n  When ALIGNED (futures confirms options): {aligned_acc:.1f}% accuracy")
        print(f"  When CONFLICTED (futures disagrees):     {conflict_acc:.1f}% accuracy")
        print(f"  Difference: {aligned_acc - conflict_acc:.1f} percentage points")
        print(f"\n  [RECOMMENDATION] Filter trades to only take ALIGNED signals:")
        print(f"    - Would improve accuracy from {original_accuracy['accuracy']}% to {aligned_acc:.1f}%")
        print(f"    - Trade count reduced from {original_accuracy['total']} to {futures_aligned}")
    
    # Sample predictions
    print("\n" + "=" * 70)
    print("  SAMPLE PREDICTIONS (Last 10)")
    print("=" * 70)
    
    for row in data_with_outcomes[-10:]:
        time = row['timestamp'][-8:]
        spot = row['spot_price']
        orig = row['original_score']
        new = row['new_score_combined']
        actual = row['actual_direction']
        futures_oi = row.get('futures_oi_change', 0) or 0
        
        orig_pred = "BULL" if orig > 15 else "BEAR" if orig < -15 else "NEUT"
        new_pred = "BULL" if new > 15 else "BEAR" if new < -15 else "NEUT"
        actual_short = actual[:4].upper()
        
        orig_ok = "Y" if (orig_pred == "BULL" and actual == "bullish") or (orig_pred == "BEAR" and actual == "bearish") else "N" if orig_pred != "NEUT" else "-"
        new_ok = "Y" if (new_pred == "BULL" and actual == "bullish") or (new_pred == "BEAR" and actual == "bearish") else "N" if new_pred != "NEUT" else "-"
        
        print(f"  {time} | Spot:{spot:,.0f} | FutOI:{futures_oi:+8,} | Orig:{orig:+6.1f}→{orig_pred}[{orig_ok}] | New:{new:+6.1f}→{new_pred}[{new_ok}] | Actual:{actual_short}")


if __name__ == "__main__":
    run_backtest()
