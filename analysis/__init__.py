"""Analysis package — OI analysis, predictions, pattern detection, v-shape.

Submodules:
  analysis.tug_of_war      — Core OI tug-of-war analysis (was oi_analyzer.py)
  analysis.pattern_tracker  — PM reversal / shakeout detection
  analysis.v_shape          — V-shape recovery detector
  analysis.prediction       — Prediction tree engine
  analysis.momentum         — Momentum calculation re-exports
  analysis.regime_detector  — Market regime detection re-exports
  analysis.confirmation     — Signal confidence re-exports
"""

# Re-export the main entry point for convenience
from analysis.tug_of_war import analyze_tug_of_war  # noqa: F401
