"""Streaks and daily activity (BUILD_FLOW Phase 4).

``streak_cache`` is always recomputable from ``daily_study_log`` +
``users.timezone``. If the cache ever drifts, ``recompute_streak`` over the
log's dates is the repair path (Phase 7 runs it as a background job).

``study_date`` is a date in the user's own timezone — the caller converts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class StreakState:
    current_streak: int = 0
    longest_streak: int = 0
    last_study_date: date | None = None


@dataclass
class DayTotals:
    study_date: date
    total_seconds: int = 0
    questions_answered: int = 0
    correct_count: int = 0


def record_day(log: dict[date, DayTotals], study_date: date, *, seconds: int,
               answered: int, correct: int) -> DayTotals:
    """Upsert-and-accumulate one day's activity. Pure dict operation."""
    day = log.get(study_date)
    if day is None:
        day = DayTotals(study_date=study_date)
        log[study_date] = day
    day.total_seconds += max(0, seconds)
    day.questions_answered += max(0, answered)
    day.correct_count += max(0, correct)
    return day


def advance_streak(state: StreakState, study_date: date) -> StreakState:
    """Fold one study day into the streak cache."""
    last = state.last_study_date
    if last is None:
        return StreakState(current_streak=1, longest_streak=max(1, state.longest_streak), last_study_date=study_date)
    if study_date == last:
        return state  # same day: no change
    if study_date == last + timedelta(days=1):
        current = state.current_streak + 1
        return StreakState(current_streak=current, longest_streak=max(state.longest_streak, current),
                           last_study_date=study_date)
    if study_date > last:
        return StreakState(current_streak=1, longest_streak=state.longest_streak, last_study_date=study_date)
    return state  # out-of-order backfill: never corrupt the cache


def recompute_streak(dates) -> StreakState:
    """Recompute from the log's study dates. The cache must always match this."""
    unique = sorted(set(dates))
    if not unique:
        return StreakState()
    longest = current = 1
    for prev, day in zip(unique, unique[1:]):
        if day == prev + timedelta(days=1):
            current += 1
            longest = max(longest, current)
        elif day != prev:
            current = 1
    return StreakState(current_streak=current, longest_streak=longest, last_study_date=unique[-1])
