"""Spaced-repetition scheduling (BUILD_FLOW Phase 4).

V1 algorithm: exponential-stability scheduler (SM-2 spirit, FSRS-shaped
state). ``stability`` is days until recall decays, ``difficulty`` is 0..1,
``next_due_at`` drives the planner's due query. A full FSRS-4.5 parameter
fit is an explicit post-V1 upgrade; the column shapes already match it.

Behaviour contract (gated):
- correct answer  -> stability grows, next_due advances into the future
- incorrect answer -> stability collapses, next_due ~10 minutes out (re-study)
- review_count always increments
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

SUCCESS_THRESHOLD = 0.6
REVIEW_SOON_MINUTES = 10
INIT_STABILITY_DAYS = 1.0
INIT_DIFFICULTY = 0.5
MIN_STABILITY_DAYS = 0.05
MAX_STABILITY_DAYS = 365.0


@dataclass
class ChunkReviewState:
    chunk_id: str
    stability: float  # days
    difficulty: float  # 0.0-1.0
    next_due_at: datetime
    last_reviewed_at: datetime
    review_count: int


def review_chunk(current: ChunkReviewState | None, chunk_id: str, grade: float, now: datetime) -> ChunkReviewState:
    """Fold one graded answer into scheduling state. Pure function."""
    grade = min(1.0, max(0.0, grade))
    stability = current.stability if current else INIT_STABILITY_DAYS
    difficulty = current.difficulty if current else INIT_DIFFICULTY
    reviews = (current.review_count if current else 0) + 1

    # Difficulty drifts toward the implied difficulty of this grade.
    difficulty = min(1.0, max(0.0, difficulty + 0.3 * ((1 - grade) - difficulty)))

    if grade >= SUCCESS_THRESHOLD:
        stability = min(MAX_STABILITY_DAYS, stability * (1.3 + 2.7 * grade))
        next_due = now + timedelta(days=stability)
    else:
        stability = max(MIN_STABILITY_DAYS, stability * 0.3)
        next_due = now + timedelta(minutes=REVIEW_SOON_MINUTES)

    return ChunkReviewState(chunk_id=chunk_id, stability=stability, difficulty=difficulty,
                            next_due_at=next_due, last_reviewed_at=now, review_count=reviews)
