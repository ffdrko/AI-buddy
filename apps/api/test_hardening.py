"""Phase 7 gate tests: auth, rate limits, usage, jobs, logging context."""

import sys
import os
import time
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
sys.path.insert(0, os.path.dirname(__file__))

import pytest
from fastapi import HTTPException

from app import jobs as J
from app.auth import (
    decode_token,
    hash_password,
    mint_tokens,
    require_user,
    verify_password,
)
from app.jobs import recompute_all_streaks, stale_embedding_report
from app.ratelimit import RateLimiter, reset_limiter
from app.request_context import get_context, log_error
from app.sessions.service import ServiceDeps, ask_tutor
from app.usage import MemoryUsageRecorder, summarize
from app.ai.client import StubProvider
from app.worker import queue_position


# ------------------------------------------------------------------ auth ---
def test_password_roundtrip():
    h = hash_password("correct-horse-8")
    assert verify_password("correct-horse-8", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")


def test_jwt_roundtrip_and_types():
    tokens = mint_tokens("u1")
    assert tokens["token_type"] == "bearer"
    from app.auth import _encode
    from datetime import datetime, timedelta, timezone

    assert decode_token(tokens["access_token"], "access") == "u1"
    with pytest.raises(HTTPException):
        decode_token(tokens["access_token"], "refresh")  # wrong type
    with pytest.raises(HTTPException):
        decode_token(tokens["access_token"] + "tampered", "access")
    expired = _encode("u1", "access", datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(HTTPException):
        decode_token(expired, "access")


def test_guard_rejects_anonymous():
    with pytest.raises(HTTPException) as e:
        require_user(None)
    assert e.value.status_code == 401


# ------------------------------------------------------------------ rate ---
def test_rate_limit_trips_and_slides():
    rl = RateLimiter(max_calls=2, window_s=60)
    t = time.time()
    rl.check("u", now=t)
    rl.check("u", now=t + 1)
    with pytest.raises(HTTPException) as e:
        rl.check("u", now=t + 2)
    assert e.value.status_code == 429
    rl.check("u", now=t + 61)  # window slid: allowed again


# ----------------------------------------------------------------- usage ---
def test_summarize():
    out = summarize([
        {"model": "m1", "tokens_used": 10, "operation": "tutor"},
        {"model": "m1", "tokens_used": 5, "operation": "tutor"},
        {"model": "m2", "tokens_used": 7, "operation": "evaluate"},
    ])
    assert out["total_tokens"] == 22 and out["total_calls"] == 3
    assert out["by_model"]["m1"] == {"tokens": 15, "calls": 2}


class MiniStore:
    def get_document(self, i):
        return {"id": "d1", "user_id": "u1", "status": "ready"}

    def get_embedding_models(self, doc):
        return ["text-embedding-3-small"]

    def vector_search(self, doc, vec, top_k):
        return [("k1", 0.1)]

    def get_chunk_states(self, u, ids):
        return {}

    def get_chunks_by_ids(self, ids):
        return {"k1": {"chunk_id": "k1", "content": "Photosynthesis in chloroplasts.",
                       "section_heading": "Bio", "page_start": 1, "page_end": 1}}


def test_tutor_call_is_logged_with_model_and_tokens():
    rec = MemoryUsageRecorder()
    deps = ServiceDeps(store=MiniStore(), ai_provider=StubProvider(),
                       embed_fn=lambda texts, m, d: [[0.1] * d], usage_sink=rec)
    out = ask_tutor(deps, user_id="u1", document_id="d1", query="What?", history=[])
    assert out["source_chunk_ids"]
    assert len(rec.entries) == 1
    e = rec.entries[0]
    assert e["operation"] == "tutor" and e["user_id"] == "u1" and e["document_id"] == "d1"
    assert e["model"] and e["tokens_used"] > 0


def test_usage_sink_failure_never_breaks_loop():
    def boom(entry):
        raise RuntimeError("sink down")

    deps = ServiceDeps(store=MiniStore(), ai_provider=StubProvider(),
                       embed_fn=lambda texts, m, d: [[0.1] * d], usage_sink=boom)
    ask_tutor(deps, user_id="u1", document_id="d1", query="What?", history=[])  # must not raise


# ------------------------------------------------------------------ jobs ---
class JobStore:
    def __init__(self):
        self.dates = {"u1": [date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16)],
                      "u2": [date(2026, 9, 1), date(2026, 9, 10)]}
        self.cache = {"u1": {"current_streak": 99, "longest_streak": 99, "last_study_date": date(2026, 9, 16)},
                      "u2": {"current_streak": 1, "longest_streak": 1, "last_study_date": date(2026, 9, 10)}}
        self.models = {"text-embedding-3-small": 10, "old-model": 4}

    def list_user_ids(self):
        return list(self.dates)

    def get_all_study_dates(self, u):
        return self.dates[u]

    def get_streak(self, u):
        return dict(self.cache[u])

    def set_streak(self, *, user_id, current, longest, last_date):
        self.cache[user_id] = {"current_streak": current, "longest_streak": longest, "last_study_date": last_date}

    def embedding_model_counts(self):
        return dict(self.models)


def test_streak_recompute_repairs_drift():
    store = JobStore()
    out = recompute_all_streaks(store)
    assert out == {"checked": 2, "fixed": 1}
    assert store.cache["u1"]["current_streak"] == 3  # repaired to the log
    assert store.cache["u2"]["current_streak"] == 1  # untouched


def test_stale_embedding_report():
    out = stale_embedding_report(JobStore(), "text-embedding-3-small")
    assert out["stale_rows"] == 4 and out["stale_models"] == ["old-model"]


# ------------------------------------------------------- misc gates ---
def test_queue_position_none_without_redis():
    assert queue_position("doc-xyz") is None


def test_request_context_defaults_and_logging():
    assert get_context() == {}
    log_error("probe %s", "ok")  # must not crash without context
    reset_limiter()
