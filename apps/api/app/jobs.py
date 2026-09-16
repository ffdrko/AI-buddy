"""Background jobs (BUILD_FLOW Phase 7).

- ``recompute_all_streaks``: repairs ``streak_cache`` drift from the log.
  Run daily; the cache must always match ``recompute_streak`` output.
- ``stale_embedding_report``: flags ``embeddings`` rows whose ``model_name``
  differs from the current model (invalid for retrieval per invariant 6).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("api.jobs")


def recompute_all_streaks(store: Any) -> dict:
    from .learning.streaks import recompute_streak

    fixed = 0
    checked = 0
    for user_id in store.list_user_ids():
        checked += 1
        dates = store.get_all_study_dates(user_id)
        expected = recompute_streak(dates)
        current = store.get_streak(user_id)
        if (current["current_streak"], current["longest_streak"], current["last_study_date"]) != (
            expected.current_streak, expected.longest_streak, expected.last_study_date
        ):
            store.set_streak(user_id=user_id, current=expected.current_streak,
                             longest=expected.longest_streak, last_date=expected.last_study_date)
            fixed += 1
            logger.info("streak repaired user=%s", user_id)
    return {"checked": checked, "fixed": fixed}


def stale_embedding_report(store: Any, current_model: str) -> dict:
    counts = store.embedding_model_counts()
    stale = {model: n for model, n in counts.items() if model != current_model}
    return {"current_model": current_model, "counts": counts,
            "stale_rows": sum(stale.values()), "stale_models": sorted(stale)}
