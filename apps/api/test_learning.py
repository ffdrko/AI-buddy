"""Phase 4 gate tests (BUILD_FLOW Phase 4). Pure domain logic, no DB."""

import random
import sys
import os
from datetime import date, timedelta, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
sys.path.insert(0, os.path.dirname(__file__))

from app.learning.mastery import (
    TopicMastery,
    find_weaknesses,
    mastery_display,
    update_mastery,
)
from app.learning.scheduling import review_chunk
from app.learning.streaks import (
    DayTotals,
    StreakState,
    advance_streak,
    recompute_streak,
    record_day,
)


# ---------------------------------------------------------------- mastery ---
def test_mastery_known_sequence():
    s = None
    for g in (1.0, 1.0, 1.0):
        s = update_mastery(s, "photo", g)
    assert s.evidence_count == 3
    assert 0.5 < s.mastery_score <= 1.0
    assert s.confidence == 1 - 1 / (1 + 3)


def test_first_answer_never_decides():
    s = update_mastery(None, "x", 0.0)
    assert s.mastery_score == 0.5, "cold-start prior must survive a single answer"
    assert s.evidence_count == 1
    s = update_mastery(None, "x", 1.0)
    assert s.mastery_score == 0.5


def test_cold_start_hides_percent():
    d = mastery_display(update_mastery(None, "x", 0.0))
    assert d == {"band": "emerging", "show_percent": False, "score": None,
                 "confidence": d["confidence"], "evidence_count": 1}
    s = TopicMastery(concept="x", mastery_score=0.9, evidence_count=5, confidence=0.8)
    d = mastery_display(s)
    assert d["show_percent"] and d["score"] == 0.9 and d["band"] == "mastered"


def test_weakness_query_shape():
    states = [
        TopicMastery("a", 0.1, 5, 0.8),   # weak
        TopicMastery("b", 0.4, 3, 0.7),   # weak
        TopicMastery("c", 0.2, 2, 0.6),   # low evidence -> excluded
        TopicMastery("d", 0.9, 9, 0.9),   # strong -> excluded
        TopicMastery("e", 0.49, 10, 0.9),  # weak
    ]
    weak = find_weaknesses(states)
    assert [w.concept for w in weak] == ["a", "b", "e"]
    assert find_weaknesses([TopicMastery("c", 0.2, 2, 0.6)]) == []


# -------------------------------------------------------------- scheduling --
def test_next_due_advances_both_ways():
    now = datetime.now(timezone.utc)
    ok = review_chunk(None, "c1", 1.0, now)
    assert ok.next_due_at > now + timedelta(hours=20), "correct -> days out"
    assert ok.review_count == 1
    bad = review_chunk(None, "c1", 0.0, now)
    assert timedelta(0) < (bad.next_due_at - now) <= timedelta(minutes=11), "incorrect -> minutes out"
    assert bad.stability < ok.stability


def test_repeated_success_compounds():
    now = datetime.now(timezone.utc)
    s = None
    stabs = []
    for _ in range(3):
        s = review_chunk(s, "c1", 1.0, now)
        stabs.append(s.stability)
    assert stabs == sorted(stabs) and len(set(round(x, 6) for x in stabs)) == 3
    assert s.review_count == 3


# ----------------------------------------------------------------- streaks --
def test_streak_basic_flow():
    d0 = date(2026, 9, 1)
    s = StreakState()
    s = advance_streak(s, d0)
    assert (s.current_streak, s.longest_streak) == (1, 1)
    s = advance_streak(s, d0)  # same day
    assert s.current_streak == 1
    s = advance_streak(s, d0 + timedelta(days=1))
    assert s.current_streak == 2
    s = advance_streak(s, d0 + timedelta(days=5))  # gap: broken
    assert (s.current_streak, s.longest_streak, s.last_study_date) == (1, 2, d0 + timedelta(days=5))
    s2 = advance_streak(s, d0)  # backfill: ignored
    assert s2 == s


def test_recompute_matches_cache_on_random_histories():
    rng = random.Random(42)
    base = date(2026, 1, 1)
    for trial in range(50):
        days = sorted({base + timedelta(days=rng.randint(0, 60)) for _ in range(rng.randint(0, 20))})
        incremental = StreakState()
        for d in days:
            incremental = advance_streak(incremental, d)
        recomputed = recompute_streak(days)
        assert incremental == recomputed, f"drift on {days}: {incremental} vs {recomputed}"


def test_record_day_accumulates():
    log: dict[date, DayTotals] = {}
    d = date(2026, 9, 16)
    record_day(log, d, seconds=600, answered=5, correct=3)
    record_day(log, d, seconds=300, answered=2, correct=2)
    assert log[d].total_seconds == 900
    assert log[d].questions_answered == 7
    assert log[d].correct_count == 5
