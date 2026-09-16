"""Session Planner (BUILD_FLOW Phase 3).

Proposes an ordered chunk list for a session. The plan is a SUGGESTION —
``validate_plan`` enforces backend invariants (known chunk ids only, capped
by available time) and Phase 5 may override it entirely.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .client import CHAT_MODEL_DEFAULT, estimate_tokens, get_provider
from .schemas import StudyPlan, PlanRequest

MINUTES_PER_CHUNK = 5

_SYSTEM = (
    "You plan a study session. Reply with JSON only: "
    '{"recommended_chunk_ids": [str], "estimated_duration_minutes": int, "rationale": str}. '
    "Recommend ONLY ids from the candidate list. Prioritise overdue reviews and weak concepts. "
    "Fit the plan within the available minutes."
)


def plan_session(req: PlanRequest, provider=None, model: str = CHAT_MODEL_DEFAULT) -> StudyPlan:
    prov = get_provider(provider)
    if prov.name == "stub":
        plan = _stub_plan(req)
    else:
        plan = _llm_plan(req, prov, model)
    return validate_plan(plan, req)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _stub_plan(req: PlanRequest) -> StudyPlan:
    now = datetime.now(timezone.utc)
    weak = {t.concept for t in req.user_topic_state if t.mastery_score < 0.5}
    state = {c.chunk_id: c for c in req.user_chunk_state}

    def overdue_days(cid: str) -> float:
        nxt = _parse_dt(state[cid].next_due_at) if cid in state else None
        if nxt is None:
            return float("inf") if req.session_goal in ("explore", "mixed") else 0.0
        return max(0.0, (now - nxt).total_seconds() / 86400.0)

    def covers_weak(cid: str) -> bool:
        return bool(set(req.chunk_concepts.get(cid, [])) & weak)

    if req.session_goal == "weak_focus":
        ranked = sorted(req.chunk_ids, key=lambda c: (not covers_weak(c), -overdue_days(c) if overdue_days(c) != float("inf") else 0.0))
    elif req.session_goal == "reinforce":
        ranked = sorted(req.chunk_ids, key=lambda c: (overdue_days(c) == 0.0, -(overdue_days(c) if overdue_days(c) != float("inf") else 0.0)))
    elif req.session_goal == "explore":
        ranked = sorted(req.chunk_ids, key=lambda c: (state.get(c).review_count if c in state else -1,))
    else:  # mixed: weak coverage first, then overdue, then unseen
        ranked = sorted(
            req.chunk_ids,
            key=lambda c: (
                not covers_weak(c),
                overdue_days(c) == 0.0,
                -(overdue_days(c) if overdue_days(c) != float("inf") else 999.0),
            ),
        )
    capacity = max(1, req.available_minutes // MINUTES_PER_CHUNK)
    chosen = ranked[:capacity]
    rationale = (
        f"goal={req.session_goal} weak_concepts={len(weak)} "
        f"chose {len(chosen)}/{len(req.chunk_ids)} chunks within {req.available_minutes}min"
    )
    return StudyPlan(recommended_chunk_ids=chosen,
                     estimated_duration_minutes=len(chosen) * MINUTES_PER_CHUNK,
                     rationale=rationale, model="stub-planner-v1",
                     tokens_used=estimate_tokens(rationale))


def _llm_plan(req: PlanRequest, prov, model: str) -> StudyPlan:
    topics = "\n".join(f"- {t.concept}: mastery={t.mastery_score} conf={t.confidence}" for t in req.user_topic_state) or "none"
    chunks = "\n".join(
        f"- {c.chunk_id}: due={c.next_due_at} reviews={c.review_count} concepts={req.chunk_concepts.get(c.chunk_id, [])}"
        for c in req.user_chunk_state
    ) or "none"
    user = (
        f"Goal: {req.session_goal}\nAvailable minutes: {req.available_minutes}\n"
        f"Candidates: {req.chunk_ids}\nTopic state:\n{topics}\nChunk state:\n{chunks}"
    )
    text, tokens = prov.chat(system=_SYSTEM, user=user, model=model, timeout_s=30, max_tokens=500)
    try:
        data = json.loads(re.search(r"\{.*\}", text, re.DOTALL).group(0))  # type: ignore[union-attr]
        return StudyPlan(
            recommended_chunk_ids=list(data.get("recommended_chunk_ids", [])),
            estimated_duration_minutes=int(data.get("estimated_duration_minutes", 0)),
            rationale=str(data.get("rationale", "")),
            model=model, tokens_used=tokens,
        )
    except Exception as e:
        raise ValueError(f"planner returned unparseable output: {e}") from e


def validate_plan(plan: StudyPlan, req: PlanRequest) -> StudyPlan:
    """Backend guard: known ids only, preserve order, cap by available time."""
    seen: list[str] = []
    for cid in plan.recommended_chunk_ids:
        if cid in req.chunk_ids and cid not in seen:
            seen.append(cid)
    capacity = max(1, req.available_minutes // MINUTES_PER_CHUNK)
    capped = seen[:capacity]
    return StudyPlan(
        recommended_chunk_ids=capped,
        estimated_duration_minutes=min(plan.estimated_duration_minutes, req.available_minutes),
        rationale=plan.rationale,
        model=plan.model,
        tokens_used=plan.tokens_used,
    )
