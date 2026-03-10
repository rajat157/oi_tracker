"""Repository for prediction engine nodes and paths."""

from __future__ import annotations

from db.base_repo import BaseRepository


class PredictionRepository(BaseRepository):
    """CRUD for prediction_nodes and prediction_paths tables.

    Delegates to legacy database.py functions during the migration period.
    """

    # NOTE: prediction_engine.py manages its own DB calls directly.
    # These wrappers exist so that callers can start using the repo
    # pattern.  Full SQL will move here in Phase 4.
    pass
