"""Analysis package — organized re-exports from oi_analyzer.py.

During the migration, functions live in oi_analyzer.py and are re-exported
through these sub-modules for a cleaner interface. Phase 4 will move the
actual code here.
"""

# Re-export the main entry point for convenience
from oi_analyzer import analyze_tug_of_war  # noqa: F401
