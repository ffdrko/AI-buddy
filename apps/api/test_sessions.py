"""Phase 5 gate tests: full session lifecycle on an in-memory store."""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from app.ai.client import StubProvider
from app.sessions.service import (
    ServiceDeps,
    ask_tutor,
    end_session,
    get_next_question,
    get_progress_summary,
    get_session_summary,
    pregenerate_questions,
    rerank_candidates,
    start_session,
    submit_answer,
)


class FakeStore:
    def __init__(self):
        self.docs = {"d1": {"id": "d1", "user_id": "u1", "status": "ready", "title": "Bio"}}
        self.chunks = {
            "k1": {"chunk_id": "k1", "chunk_index": 0, "content": "Photosynthesis converts light energy into chemical energy in chloroplasts.",
                   "section_heading": "Biology", "page_start": 1, "page_end": 1},
            "k2": {"chunk_id": "k2", "chunk_index": 1, "content": "Cellular respiration releases energy from glucose in mitochondria.",
                   "section_heading": "Biology", "page_start": 2, "page_end": 2},
        }
        self.concepts = {"k1": [("photosynthesis", 0.9)], "k2": [("respiration", 0.9)]}
        self.sessions: dict[str, dict] = {}
        self.questions: dict[str, dict] = {}
        self.answers: dict[str, dict] = {}
        self.topics: dict[tuple[str, str], dict] = {}
        self.chunk_states: dict[tuple[str, str], dict] = {}
        self.logs: dict = {}
        self.streak = {"current_streak": 0, "longest_streak": 0, "last_study_date": None}
        self._seq = 0

    def _nid(self, p):
        self._seq += 1
        return f"{p}-{self._seq}"

    def get_document(self, i):
        return self.docs.get(i)

    def get_user_timezone(self, u):
        return "UTC"

    def create_session(self, *, user_id, document_id, planned_minutes):
        sid = self._nid("s")
        self.sessions[sid] = {"id": sid, "user_id": user_id, "document_id": document_id,
                              "status": "active", "planned_duration_minutes": planned_minutes,
                              "started_at": datetime.now(timezone.utc)}
        return {"id": sid, "status": "active"}

    def get_session(self, i):
        return self.sessions.get(i)

    def set_session_ended(self, i, *, ended_at, actual_seconds):
        self.sessions[i].update(status="completed", ended_at=ended_at, actual_seconds=actual_seconds)

    def list_chunks(self, doc):
        return [self.chunks[k] for k in ("k1", "k2")]

    def get_chunk_concepts(self, ids):
        return {cid: list(self.concepts.get(cid, [])) for cid in ids}

    def get_embedding_models(self, doc):
        return ["text-embedding-3-small"]

    def vector_search(self, doc, vec, top_k):
        return [("k1", 0.1), ("k2", 0.2)][:top_k]

    def add_question(self, **kw):
        qid = self._nid("q")
        self.questions[qid] = {"id": qid, **kw, "generated_at": datetime.now(timezone.utc)}
        return {"id": qid}

    def next_unanswered(self, sid):
        answered = {a["question_id"] for a in self.answers.values() if a["session_id"] == sid}
        for q in sorted(self.questions.values(), key=lambda r: r["generated_at"]):
            if q["session_id"] == sid and q["id"] not in answered:
                return q
        return None

    def count_questions(self, sid):
        return sum(1 for q in self.questions.values() if q["session_id"] == sid)

    def question_chunk_least_asked(self, sid, doc):
        counts: dict[str, int] = {}
        for q in self.questions.values():
            if q["session_id"] == sid:
                counts[q["chunk_id"]] = counts.get(q["chunk_id"], 0) + 1
        return self.chunks[min(self.chunks, key=lambda k: counts.get(k, 0))]

    def get_question(self, i):
        return self.questions.get(i)

    def get_chunks_by_ids(self, ids):
        return {cid: self.chunks[cid] for cid in ids if cid in self.chunks}

    def add_answer(self, **kw):
        aid = self._nid("a")
        self.answers[aid] = {"id": aid, **kw, "llm_grade": None, "llm_feedback": None, "llm_model": None}
        return {"id": aid}

    def set_answer_grade(self, aid, *, grade, feedback, model, graded_at):
        self.answers[aid].update(llm_grade=grade, llm_feedback=feedback, llm_model=model)

    def list_answers(self, sid):
        return [a for a in self.answers.values() if a["session_id"] == sid]

    def get_topic_states(self, u):
        return [{"concept": c, **v} for (uu, c), v in self.topics.items() if uu == u]

    def upsert_topic_state(self, *, user_id, concept, mastery, evidence, confidence):
        self.topics[(user_id, concept)] = {"mastery_score": mastery, "evidence_count": evidence, "confidence": confidence}

    def get_chunk_states(self, u, ids):
        return {cid: self.chunk_states[(u, cid)] for cid in ids if (u, cid) in self.chunk_states}

    def upsert_chunk_state(self, **kw):
        u, cid = kw.pop("user_id"), kw.pop("chunk_id")
        self.chunk_states[(u, cid)] = kw

    def upsert_daily_log(self, **kw):
        key = (kw["user_id"], kw["study_date"])
        prev = self.logs.get(key, {"seconds": 0, "answered": 0, "correct": 0})
        self.logs[key] = {"seconds": prev["seconds"] + kw["seconds"], "answered": prev["answered"] + kw["answered"],
                          "correct": prev["correct"] + kw["correct"]}

    def get_streak(self, u):
        return dict(self.streak)

    def set_streak(self, *, user_id, current, longest, last_date):
        self.streak = {"current_streak": current, "longest_streak": longest, "last_study_date": last_date}

    def list_sessions(self, u, limit=10):
        return [{"id": s["id"], "document_id": s["document_id"], "status": s["status"],
                 "planned_duration_minutes": s["planned_duration_minutes"],
                 "started_at": None, "ended_at": None}
                for s in self.sessions.values() if s["user_id"] == u][:limit]

    def count_answers(self, sid):
        return sum(1 for a in self.answers.values() if a["session_id"] == sid)

    def get_daily_logs(self, u, since):
        return []


def _deps(store=None):
    store = store or FakeStore()
    return ServiceDeps(store=store, ai_provider=StubProvider(),
                       embed_fn=lambda texts, model, dim: [[0.1] * dim for _ in texts])


# ------------------------------------------------------------------ gates ---
def test_full_session_lifecycle():
    deps = _deps()
    started = start_session(deps, user_id="u1", document_id="d1", available_minutes=15, session_goal="mixed")
    sid = started["session_id"]
    assert started["estimated_duration_minutes"] <= 15
    assert pregenerate_questions(deps, sid, started["recommended_chunk_ids"])

    # next-question leaks no answers or grounding
    q1 = get_next_question(deps, user_id="u1", session_id=sid)
    assert "correct_option_id" not in q1 and "rubric_chunk_ids" not in q1
    assert q1["options"] is None or all(set(o) <= {"id", "text"} for o in q1["options"])

    # answer Q1 correctly-ish (free text sharing rubric terms) and Q2
    a1 = submit_answer(deps, user_id="u1", session_id=sid, question_id=q1["id"],
                       response_text="Photosynthesis converts light energy in chloroplasts",
                       selected_option_id=None, time_seconds=60, hints_used=0)
    assert a1["grade"] is not None and a1["feedback"] and a1["correct_answer_explanation"]
    q2 = get_next_question(deps, user_id="u1", session_id=sid)
    assert q2["id"] != q1["id"]
    submit_answer(deps, user_id="u1", session_id=sid, question_id=q2["id"],
                  response_text="Completely unrelated words here", selected_option_id=None,
                  time_seconds=30, hints_used=1)

    # answers log complete; evaluator model recorded; mastery written by backend
    answers = deps.store.list_answers(sid)
    assert len(answers) == 2 and all(a["llm_model"] for a in answers)
    assert all(a["llm_grade"] is not None for a in answers)
    topics = {t["concept"]: t for t in deps.store.get_topic_states("u1")}
    assert topics["photosynthesis"]["evidence_count"] >= 1
    assert deps.store.get_streak("u1")["current_streak"] == 1

    summary = end_session(deps, user_id="u1", session_id=sid)
    assert summary["questions_answered"] == 2
    # summary derivable from the log
    grades = [a["llm_grade"] for a in answers]
    expect_correct = sum(1 for g in grades if g >= 0.6)
    assert summary["correct_count"] == expect_correct
    assert summary["accuracy"] == round(expect_correct / 2, 3)
    assert summary["study_time_seconds"] == 90
    assert "photosynthesis" in summary["concepts_covered"]
    with pytest.raises(ValueError):
        get_next_question(deps, user_id="u1", session_id=sid)


def test_ownership_and_validation():
    deps = _deps()
    sid = start_session(deps, user_id="u1", document_id="d1", available_minutes=10, session_goal="mixed")["session_id"]
    with pytest.raises(LookupError):
        get_next_question(deps, user_id="intruder", session_id=sid)
    q = get_next_question(deps, user_id="u1", session_id=sid)
    with pytest.raises(ValueError):
        submit_answer(deps, user_id="u1", session_id=sid, question_id=q["id"],
                      response_text=None, selected_option_id=None, time_seconds=5)
    with pytest.raises(LookupError):
        submit_answer(deps, user_id="u1", session_id=sid, question_id="q-nope",
                      response_text="x", selected_option_id=None, time_seconds=5)


def test_on_demand_generation_after_queue_drained():
    deps = _deps()
    sid = start_session(deps, user_id="u1", document_id="d1", available_minutes=5, session_goal="mixed")["session_id"]
    seen = set()
    for _ in range(4):  # more than the 3 pregenerated
        q = get_next_question(deps, user_id="u1", session_id=sid)
        seen.add(q["id"])
        submit_answer(deps, user_id="u1", session_id=sid, question_id=q["id"],
                      response_text="photosynthesis light energy chloroplasts glucose mitochondria",
                      selected_option_id=None, time_seconds=10)
    assert len(seen) == 4, "queue must regenerate on demand"


def test_tutor_ask_grounded_and_model_checked():
    deps = _deps()
    out = ask_tutor(deps, user_id="u1", document_id="d1", query="What is photosynthesis?", history=[])
    assert out["source_chunk_ids"], "never a response without grounding"
    assert out["llm_model"]
    deps.embedding_model = "other-model"
    with pytest.raises(ValueError, match="model mismatch"):
        ask_tutor(deps, user_id="u1", document_id="d1", query="hi", history=[])


def test_rerank_boosts_unseen_and_due():
    now = datetime.now(timezone.utc)
    states = {"k1": {"review_count": 10, "next_due_at": now.replace(year=now.year + 1)}, "k2": {"review_count": 0, "next_due_at": None}}
    assert rerank_candidates([("k1", 0.01), ("k2", 0.05)], states, now)[0] == "k2"


def test_end_is_idempotent_and_summary_readable():
    deps = _deps()
    sid = start_session(deps, user_id="u1", document_id="d1", available_minutes=10, session_goal="mixed")["session_id"]
    q = get_next_question(deps, user_id="u1", session_id=sid)
    submit_answer(deps, user_id="u1", session_id=sid, question_id=q["id"],
                  response_text="photosynthesis light energy chloroplasts", selected_option_id=None, time_seconds=20)
    first = end_session(deps, user_id="u1", session_id=sid)
    second = end_session(deps, user_id="u1", session_id=sid)
    assert first["questions_answered"] == second["questions_answered"] == 1
    via_get = get_session_summary(deps, user_id="u1", session_id=sid)
    assert via_get["questions_answered"] == 1 and via_get["status"] == "completed"
    with pytest.raises(LookupError):
        get_session_summary(deps, user_id="intruder", session_id=sid)


def test_progress_summary_shape():
    deps = _deps()
    sid = start_session(deps, user_id="u1", document_id="d1", available_minutes=10, session_goal="mixed")["session_id"]
    q = get_next_question(deps, user_id="u1", session_id=sid)
    submit_answer(deps, user_id="u1", session_id=sid, question_id=q["id"],
                  response_text="photosynthesis light energy chloroplasts", selected_option_id=None, time_seconds=20)
    out = get_progress_summary(deps, user_id="u1")
    assert out["streak"]["current_streak"] == 1
    assert any(t["concept"] == "photosynthesis" for t in out["topics"])
    assert out["recent_sessions"] and out["recent_sessions"][0]["questions_answered"] == 1


def test_tutor_returns_displayable_sources():
    deps = _deps()
    out = ask_tutor(deps, user_id="u1", document_id="d1", query="What is photosynthesis?", history=[])
    assert out["sources"], "UI needs headings/pages, not raw chunk ids"
    assert all(s["section_heading"] for s in out["sources"])
