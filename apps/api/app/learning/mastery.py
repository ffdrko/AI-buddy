"""Mastery model (BUILD_FLOW Phase 4).

``user_topic_state`` is a cache/projection — ground truth is always
recomputable from ``answers`` + ``chunk_concepts``. These functions are pure
(no DB): Phase 5 persists their output. Grades are observations; a single
answer never decides mastery (weight grows with evidence).
"""

from __future__ import annotations

from dataclasses import dataclass

WEAKNESS_THRESHOLD = 0.5
MIN_EVIDENCE = 3  # below this, UI shows a confidence band, never a raw %
COLD_START_PRIOR = 0.5


@dataclass
class TopicMastery:
    concept: str
    mastery_score: float  # 0.0-1.0
    evidence_count: int
    confidence: float  # 0.0-1.0, grows with evidence


def update_mastery(current: TopicMastery | None, concept: str, grade: float) -> TopicMastery:
    """Bayesian-ish update. ``grade`` is 0.0-1.0 from the Evaluator."""
    grade = min(1.0, max(0.0, grade))
    prior = current.mastery_score if current else COLD_START_PRIOR
    evidence = current.evidence_count if current else 0
    alpha = min(evidence / 10.0, 1.0)  # trust data more as evidence grows
    new_score = (1 - alpha) * prior + alpha * grade
    new_evidence = evidence + 1
    confidence = 1 - (1 / (1 + new_evidence))
    return TopicMastery(concept=concept, mastery_score=new_score,
                        evidence_count=new_evidence, confidence=confidence)


def mastery_display(state: TopicMastery) -> dict:
    """What the UI may show. Cold-start states never expose a raw percent."""
    if state.evidence_count < MIN_EVIDENCE:
        return {"band": "emerging", "show_percent": False, "score": None,
                "confidence": state.confidence, "evidence_count": state.evidence_count}
    if state.mastery_score < WEAKNESS_THRESHOLD:
        band = "developing"
    elif state.mastery_score < 0.8:
        band = "proficient"
    else:
        band = "mastered"
    return {"band": band, "show_percent": True, "score": round(state.mastery_score, 3),
            "confidence": state.confidence, "evidence_count": state.evidence_count}


def find_weaknesses(states: list[TopicMastery], limit: int = 5) -> list[TopicMastery]:
    """Weakness is a query, not a table: low mastery with enough evidence."""
    weak = [s for s in states if s.mastery_score < WEAKNESS_THRESHOLD and s.evidence_count >= MIN_EVIDENCE]
    return sorted(weak, key=lambda s: s.mastery_score)[:limit]


# Canonical SQL for the real store (mirrors find_weaknesses exactly).
WEAKNESS_SQL = """
SELECT concept, mastery_score, evidence_count
FROM user_topic_state
WHERE user_id = %(user_id)s
  AND mastery_score < 0.5
  AND evidence_count >= 3
ORDER BY mastery_score ASC
LIMIT 5;
"""
