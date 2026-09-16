"""Session lifecycle orchestrator (BUILD_FLOW Phase 5).

Wires Content + AI + Learning engines: plan -> generate -> evaluate ->
mastery/scheduling/streak projections. All functions take explicit deps and
return plain dicts; the routers are thin. LLM output is always stored with
provenance (model version) and only ever *read* by backend logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger("api.sessions")

PREGENERATE_COUNT = 3
TUTOR_TOP_K = 8
TUTOR_CONTEXT_N = 4
QUESTION_TYPE_ROTATION = ("mcq", "free_text", "true_false")


@dataclass
class ServiceDeps:
    store: Any  # SessionStore
    ai_provider: Any = None  # resolved by ai.client.get_provider
    embed_fn: Any = None  # (texts, model, dim) -> vectors
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    threshold: float = 0.6
    usage_sink: Any = None  # callable(entry) — Phase 7 cost tracking


def _record(deps: ServiceDeps, *, user_id: str, operation: str, model: str,
            tokens_used: int, session_id: str | None = None, document_id: str | None = None) -> None:
    if deps.usage_sink is None:
        return
    try:
        deps.usage_sink({"user_id": user_id, "session_id": session_id, "document_id": document_id,
                         "operation": operation, "model": model, "tokens_used": tokens_used})
    except Exception as e:  # noqa: BLE001 - usage must never break the loop
        logger.warning("usage record failed op=%s err=%s", operation, e)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _study_date(tz_name: str | None) -> date:
    try:
        tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    except Exception:
        tz = timezone.utc
    return _now().astimezone(tz).date()


# ------------------------------------------------------------ start -----
def start_session(deps: ServiceDeps, *, user_id: str, document_id: str,
                  available_minutes: int, session_goal: str) -> dict:
    from ..ai.planner import plan_session
    from ..ai.schemas import ChunkState, PlanRequest, TopicState

    doc = deps.store.get_document(document_id)
    if doc is None or doc["user_id"] != user_id:
        raise LookupError("document not found")
    if doc["status"] != "ready":
        raise ValueError(f"document is not ready (status={doc['status']})")

    chunks = deps.store.list_chunks(document_id)
    if not chunks:
        raise ValueError("document has no chunks")
    topic_rows = deps.store.get_topic_states(user_id)
    chunk_ids = [c["chunk_id"] for c in chunks]
    state_rows = deps.store.get_chunk_states(user_id, chunk_ids)
    concepts = deps.store.get_chunk_concepts(chunk_ids)

    plan = plan_session(PlanRequest(
        user_id=user_id, document_id=document_id,
        user_topic_state=[TopicState(concept=r["concept"], mastery_score=r["mastery_score"], confidence=r["confidence"]) for r in topic_rows],
        user_chunk_state=[ChunkState(chunk_id=cid, next_due_at=r["next_due_at"].isoformat() if r.get("next_due_at") else None,
                                     review_count=r.get("review_count", 0)) for cid, r in state_rows.items()],
        chunk_ids=chunk_ids, chunk_concepts={k: [c for c, _ in v] for k, v in concepts.items()},
        available_minutes=available_minutes, session_goal=session_goal), provider=deps.ai_provider)

    session = deps.store.create_session(user_id=user_id, document_id=document_id, planned_minutes=available_minutes)
    _record(deps, user_id=user_id, operation="plan", model=plan.model, tokens_used=plan.tokens_used,
            session_id=session["id"], document_id=document_id)
    return {"session_id": session["id"], "estimated_duration_minutes": plan.estimated_duration_minutes,
            "recommended_chunk_ids": plan.recommended_chunk_ids}


def pregenerate_questions(deps: ServiceDeps, session_id: str, chunk_ids: list[str], n: int = PREGENERATE_COUNT) -> list[str]:
    """Generate the first questions up front. Failures are logged, never fatal."""
    from ..ai.questions import generate_question
    from ..ai.schemas import GenerateQuestionRequest

    ids: list[str] = []
    by_id = deps.store.get_chunks_by_ids(chunk_ids)
    session = deps.store.get_session(session_id) or {}
    for i, cid in enumerate(chunk_ids[:n]):
        chunk = by_id.get(cid)
        if chunk is None:
            continue
        try:
            concepts = deps.store.get_chunk_concepts([cid]).get(cid, [])
            q = generate_question(GenerateQuestionRequest(
                chunk_id=cid, chunk_content=chunk["content"], section_heading=chunk.get("section_heading"),
                question_type=QUESTION_TYPE_ROTATION[i % 3],
                concepts=[c for c, _ in concepts], avoid_question_ids=[]), provider=deps.ai_provider)
            row = deps.store.add_question(
                session_id=session_id, chunk_id=cid, question_text=q.question_text,
                question_type=q.question_type,
                options=[o.model_dump() for o in q.options] if q.options else None,
                correct_option_id=q.correct_option_id, rubric_chunk_ids=q.rubric_chunk_ids, llm_model=q.model)
            ids.append(row["id"])
            _record(deps, user_id=session.get("user_id", ""), operation="generate", model=q.model,
                    tokens_used=q.tokens_used, session_id=session_id)
        except Exception as e:  # noqa: BLE001 - pregen must not block the session
            logger.warning("pregen failed session=%s chunk=%s err=%s", session_id, cid, e)
    return ids


# -------------------------------------------------------------- next -----
def public_question(deps: ServiceDeps, question: dict) -> dict:
    """Strip answers and grounding before sending to the client."""
    chunks = deps.store.get_chunks_by_ids([question["chunk_id"]])
    heading = (chunks.get(question["chunk_id"]) or {}).get("section_heading")
    return {"id": question["id"], "question_text": question["question_text"],
            "question_type": question["question_type"], "options": question["options"],
            "section_heading": heading}


def get_next_question(deps: ServiceDeps, *, user_id: str, session_id: str) -> dict:
    from ..ai.questions import generate_question
    from ..ai.schemas import GenerateQuestionRequest

    session = deps.store.get_session(session_id)
    if session is None or session["user_id"] != user_id:
        raise LookupError("session not found")
    if session["status"] != "active":
        raise ValueError(f"session is {session['status']}")

    pending = deps.store.next_unanswered(session_id)
    if pending is not None:
        return public_question(deps, pending)

    # Queue empty: generate on demand from the least-asked chunk.
    chunk = deps.store.question_chunk_least_asked(session_id, session["document_id"])
    if chunk is None:
        raise ValueError("no chunks available for question generation")
    count = deps.store.count_questions(session_id)
    concepts = deps.store.get_chunk_concepts([chunk["chunk_id"]]).get(chunk["chunk_id"], [])
    q = generate_question(GenerateQuestionRequest(
        chunk_id=chunk["chunk_id"], chunk_content=chunk["content"],
        section_heading=chunk.get("section_heading"),
        question_type=QUESTION_TYPE_ROTATION[count % 3],
        concepts=[c for c, _ in concepts], avoid_question_ids=[]), provider=deps.ai_provider)
    row_id = deps.store.add_question(
        session_id=session_id, chunk_id=chunk["chunk_id"], question_text=q.question_text,
        question_type=q.question_type, options=[o.model_dump() for o in q.options] if q.options else None,
        correct_option_id=q.correct_option_id, rubric_chunk_ids=q.rubric_chunk_ids, llm_model=q.model)["id"]
    _record(deps, user_id=user_id, operation="generate", model=q.model, tokens_used=q.tokens_used,
            session_id=session_id, document_id=session["document_id"])
    created = deps.store.get_question(row_id)
    assert created is not None
    return public_question(deps, created)


# ------------------------------------------------------------ answer -----
def submit_answer(deps: ServiceDeps, *, user_id: str, session_id: str, question_id: str,
                  response_text: str | None, selected_option_id: str | None,
                  time_seconds: int | None, hints_used: int = 0) -> dict:
    from ..ai.evaluator import evaluate_answer
    from ..ai.schemas import EvaluateRequest, RetrievedChunk
    from ..learning.mastery import TopicMastery, update_mastery
    from ..learning.scheduling import ChunkReviewState, review_chunk
    from ..learning.streaks import DayTotals, StreakState, advance_streak, record_day

    session = deps.store.get_session(session_id)
    if session is None or session["user_id"] != user_id:
        raise LookupError("session not found")
    if session["status"] != "active":
        raise ValueError(f"session is {session['status']}")
    question = deps.store.get_question(question_id)
    if question is None or question["session_id"] != session_id:
        raise LookupError("question not found in this session")
    if not response_text and not selected_option_id:
        raise ValueError("provide response_text or selected_option_id")

    now = _now()
    answer = deps.store.add_answer(question_id=question_id, user_id=user_id, session_id=session_id,
                                   response_text=response_text, selected_option_id=selected_option_id,
                                   hints_used=hints_used, time_seconds=time_seconds)

    # Evaluate against rubric grounding (observation, not truth).
    rubric = deps.store.get_chunks_by_ids(question["rubric_chunk_ids"])
    evaluation = evaluate_answer(EvaluateRequest(
        question_text=question["question_text"], user_response=response_text or f"selected: {selected_option_id}",
        selected_option_id=selected_option_id,
        rubric_chunks=[RetrievedChunk(chunk_id=cid, content=c["content"], section_heading=c.get("section_heading"))
                       for cid, c in rubric.items()],
        question_type=question["question_type"], correct_option_id=question["correct_option_id"]),
        threshold=deps.threshold, provider=deps.ai_provider)
    deps.store.set_answer_grade(answer["id"], grade=evaluation.grade, feedback=evaluation.feedback,
                                model=evaluation.model, graded_at=now)
    _record(deps, user_id=user_id, operation="evaluate", model=evaluation.model, tokens_used=evaluation.tokens_used,
            session_id=session_id, document_id=session["document_id"])

    # Learning Engine projections (backend logic owns the writes).
    concepts = deps.store.get_chunk_concepts([question["chunk_id"]]).get(question["chunk_id"], [])
    current_rows = {r["concept"]: r for r in deps.store.get_topic_states(user_id)}
    for concept, _ in concepts:
        cur = current_rows.get(concept)
        updated = update_mastery(
            TopicMastery(concept, cur["mastery_score"], cur["evidence_count"], cur["confidence"]) if cur else None,
            concept, evaluation.grade)
        deps.store.upsert_topic_state(user_id=user_id, concept=concept, mastery=updated.mastery_score,
                                      evidence=updated.evidence_count, confidence=updated.confidence)

    chunk_rows = deps.store.get_chunk_states(user_id, [question["chunk_id"]])
    cur = chunk_rows.get(question["chunk_id"])
    reviewed = review_chunk(
        ChunkReviewState(question["chunk_id"], cur["stability"] or 1.0, cur["difficulty"] if cur["difficulty"] is not None else 0.5,
                         cur["next_due_at"] or now, cur["last_reviewed_at"] or now, cur["review_count"])
        if cur and cur.get("stability") else None,
        question["chunk_id"], evaluation.grade, now)
    deps.store.upsert_chunk_state(user_id=user_id, chunk_id=question["chunk_id"], stability=reviewed.stability,
                                  difficulty=reviewed.difficulty, next_due_at=reviewed.next_due_at,
                                  last_reviewed_at=reviewed.last_reviewed_at, review_count=reviewed.review_count)

    # Daily log + streak (study_date in the user's timezone).
    study_day = _study_date(deps.store.get_user_timezone(user_id))
    correct = 1 if evaluation.is_correct else 0
    deps.store.upsert_daily_log(user_id=user_id, study_date=study_day,
                                seconds=time_seconds or 0, answered=1, correct=correct)
    streak_row = deps.store.get_streak(user_id)
    advanced = advance_streak(
        StreakState(streak_row["current_streak"], streak_row["longest_streak"], streak_row["last_study_date"]),
        study_day)
    deps.store.set_streak(user_id=user_id, current=advanced.current_streak,
                          longest=advanced.longest_streak, last_date=advanced.last_study_date)

    if question["question_type"] in ("mcq", "true_false") and question["correct_option_id"]:
        explanation = next((o["text"] for o in (question["options"] or [])
                            if o.get("id") == question["correct_option_id"]), evaluation.feedback)
    else:
        explanation = evaluation.feedback
    return {"grade": evaluation.grade, "is_correct": evaluation.is_correct,
            "feedback": evaluation.feedback, "correct_answer_explanation": explanation,
            "llm_model": evaluation.model}


# --------------------------------------------------------------- end -----
def compute_summary(deps: ServiceDeps, session_id: str) -> dict:
    """Session summary derived purely from the answers log. Read-only."""
    answers = deps.store.list_answers(session_id)
    total = len(answers)
    correct = sum(1 for a in answers if (a["llm_grade"] or 0) >= deps.threshold)
    study_seconds = sum(a["time_seconds"] or 0 for a in answers)
    chunk_ids = {deps.store.get_question(a["question_id"])["chunk_id"]
                 for a in answers if deps.store.get_question(a["question_id"])}
    concepts = deps.store.get_chunk_concepts(sorted(chunk_ids))
    covered = sorted({c for clist in concepts.values() for c, _ in clist})
    return {"accuracy": round(correct / total, 3) if total else 0.0,
            "questions_answered": total, "correct_count": correct,
            "study_time_seconds": study_seconds, "concepts_covered": covered}


def end_session(deps: ServiceDeps, *, user_id: str, session_id: str) -> dict:
    session = deps.store.get_session(session_id)
    if session is None or session["user_id"] != user_id:
        raise LookupError("session not found")
    if session["status"] == "completed":
        summary = compute_summary(deps, session_id)  # refresh-safe reread
        summary["actual_duration_seconds"] = None
        return summary
    if session["status"] != "active":
        raise ValueError(f"session is {session['status']}")

    now = _now()
    summary = compute_summary(deps, session_id)
    started = session.get("started_at")
    actual = int((now - started).total_seconds()) if started else summary["study_time_seconds"]
    deps.store.set_session_ended(session_id, ended_at=now, actual_seconds=actual)
    summary["actual_duration_seconds"] = actual
    return summary


def get_session_summary(deps: ServiceDeps, *, user_id: str, session_id: str) -> dict:
    session = deps.store.get_session(session_id)
    if session is None or session["user_id"] != user_id:
        raise LookupError("session not found")
    return {**compute_summary(deps, session_id), "status": session["status"]}


def get_progress_summary(deps: ServiceDeps, *, user_id: str) -> dict:
    """Dashboard read model: streak cache + topic states + 30d log + recents."""
    from datetime import timedelta

    streak = deps.store.get_streak(user_id)
    topics = deps.store.get_topic_states(user_id)
    logs = deps.store.get_daily_logs(user_id, _study_date(None) - timedelta(days=30))
    recent = deps.store.list_sessions(user_id, limit=10)
    for r in recent:
        r["questions_answered"] = deps.store.count_answers(r["id"])
    return {
        "user_id": user_id,
        "streak": {**streak, "last_study_date": streak["last_study_date"].isoformat()
                   if streak["last_study_date"] else None},
        "topics": topics,
        "daily_logs": logs,
        "recent_sessions": recent,
    }


# ------------------------------------------------------------- tutor -----
def rerank_candidates(scored: list[tuple[str, float]], states: dict[str, dict], now: datetime) -> list[str]:
    """Deprioritise well-known chunks; boost unseen/overdue ones. Pure."""

    def key(item: tuple[str, float]) -> tuple[float, str]:
        cid, dist = item
        st = states.get(cid)
        bonus = 0.0
        if st is None or not st.get("review_count"):
            bonus = -0.05
        elif st.get("next_due_at") and st["next_due_at"] <= now:
            bonus = -0.03
        return (dist + bonus, cid)

    return [cid for cid, _ in sorted(scored, key=key)]


def ask_tutor(deps: ServiceDeps, *, user_id: str, document_id: str,
              query: str, history: list[dict]) -> dict:
    from ..ai.schemas import ChatTurn, RetrievedChunk, TutorRequest
    from ..ai.tutor import answer_tutor

    if deps.embed_fn is None:
        from ..ingestion.llm import embed_texts as _embed

        deps.embed_fn = _embed
    doc = deps.store.get_document(document_id)
    if doc is None or doc["user_id"] != user_id:
        raise LookupError("document not found")
    models = deps.store.get_embedding_models(document_id)
    if not models:
        raise ValueError("document has no embeddings yet")
    if deps.embedding_model not in models:
        raise ValueError(f"embedding model mismatch: query={deps.embedding_model} indexed={models}")

    vectors = deps.embed_fn([query], deps.embedding_model, deps.embedding_dim)
    scored = deps.store.vector_search(document_id, vectors[0], TUTOR_TOP_K)
    if not scored:
        raise ValueError("no relevant chunks found")
    states = deps.store.get_chunk_states(user_id, [cid for cid, _ in scored])
    ordered = rerank_candidates(scored, states, _now())[:TUTOR_CONTEXT_N]
    chunks = deps.store.get_chunks_by_ids(ordered)
    retrieved = [RetrievedChunk(chunk_id=cid, content=chunks[cid]["content"],
                                section_heading=chunks[cid].get("section_heading"),
                                page_start=chunks[cid].get("page_start"),
                                page_end=chunks[cid].get("page_end"))
                 for cid in ordered if cid in chunks]
    out = answer_tutor(
        TutorRequest(
            user_query=query, retrieved_chunks=retrieved,
            conversation_history=[ChatTurn(role=t["role"], content=t["content"]) for t in history[-10:]]),
        provider=deps.ai_provider)
    _record(deps, user_id=user_id, operation="tutor", model=out.model, tokens_used=out.tokens_used,
            document_id=document_id)
    return {"response_text": out.response_text, "source_chunk_ids": out.source_chunk_ids, "llm_model": out.model,
            "sources": [{"chunk_id": r.chunk_id, "section_heading": r.section_heading,
                         "page_start": r.page_start, "page_end": r.page_end} for r in retrieved]}
