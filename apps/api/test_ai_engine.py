"""Phase 3 gate tests (BUILD_FLOW Phase 3).

All engines run on the deterministic stub provider — gates assert contract
shape (provenance, model/tokens, validation), not LLM quality.
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from app.ai import evaluator as E
from app.ai import planner as P
from app.ai import questions as Q
from app.ai import tutor as T
from app.ai.client import StubProvider, call_with_retry, get_provider
from app.ai.schemas import (
    ChatTurn,
    ChunkState,
    EvaluateRequest,
    GenerateQuestionRequest,
    PlanRequest,
    RetrievedChunk,
    TopicState,
    TutorRequest,
)

STUB = StubProvider()
CHUNKS = [
    RetrievedChunk(chunk_id="c1", content="Photosynthesis converts light energy into chemical energy in chloroplasts.", section_heading="Biology"),
    RetrievedChunk(chunk_id="c2", content="Cellular respiration releases energy from glucose in mitochondria.", section_heading="Biology"),
]


def _tracked(resp):
    assert resp.model, "every output must include model"
    assert resp.tokens_used >= 0, "every output must include tokens_used"
    return resp


# ----------------------------------------------------------------- tutor ---
def test_tutor_always_grounded():
    req = TutorRequest(user_query="What is photosynthesis?",
                       retrieved_chunks=CHUNKS,
                       conversation_history=[ChatTurn(role="user", content="hi")])
    out = _tracked(T.answer_tutor(req, provider=STUB))
    assert out.source_chunk_ids, "never a response without grounding"
    assert set(out.source_chunk_ids) <= {"c1", "c2"}
    assert out.response_text.strip()


def test_tutor_requires_chunks():
    with pytest.raises(Exception):
        TutorRequest(user_query="hi", retrieved_chunks=[])


# ------------------------------------------------------- question gen -----
def test_qgen_mcq_contract():
    req = GenerateQuestionRequest(chunk_id="c1", chunk_content=CHUNKS[0].content,
                                  section_heading="Biology", question_type="mcq",
                                  concepts=["photosynthesis"], avoid_question_ids=[])
    out = _tracked(Q.generate_question(req, provider=STUB))
    assert out.rubric_chunk_ids == ["c1"], "provenance mandatory"
    assert out.options is not None and len(out.options) == 4
    assert out.correct_option_id in {o.id for o in out.options}


def test_qgen_free_text_and_true_false():
    for qtype in ("free_text", "true_false"):
        req = GenerateQuestionRequest(chunk_id="c2", chunk_content=CHUNKS[1].content, question_type=qtype)
        out = _tracked(Q.generate_question(req, provider=STUB))
        assert out.rubric_chunk_ids == ["c2"]
        assert out.question_text.strip()


def test_qgen_refuses_without_provenance():
    with pytest.raises(Exception):
        GenerateQuestionRequest(chunk_id="", chunk_content="text", question_type="mcq")
    req = GenerateQuestionRequest(chunk_id="c1", chunk_content="   ", question_type="mcq")
    with pytest.raises(ValueError, match="non-empty chunk_id and chunk_content"):
        Q.generate_question(req, provider=STUB)


# -------------------------------------------------------------- evaluator --
def _eval_req(qtype, response, selected=None, correct=None):
    return EvaluateRequest(question_text="Q?", user_response=response, selected_option_id=selected,
                           rubric_chunks=CHUNKS, question_type=qtype, correct_option_id=correct)


def test_evaluator_mcq_correct_and_incorrect():
    good = _tracked(E.evaluate_answer(_eval_req("mcq", "A", selected="A", correct="A"), provider=STUB))
    assert (good.grade, good.is_correct) == (1.0, True)
    bad = _tracked(E.evaluate_answer(_eval_req("mcq", "B", selected="B", correct="A"), provider=STUB))
    assert (bad.grade, bad.is_correct) == (0.0, False)
    assert bad.feedback.strip()


def test_evaluator_free_text_uses_rubric():
    good = _tracked(E.evaluate_answer(
        _eval_req("free_text",
                  "Photosynthesis converts light energy into chemical energy in chloroplasts, "
                  "while cellular respiration releases energy from glucose in mitochondria"),
        provider=STUB))
    assert good.grade >= 0.6 and good.is_correct
    bad = _tracked(E.evaluate_answer(_eval_req("free_text", "I like pizza very much"), provider=STUB))
    assert bad.grade < 0.6 and not bad.is_correct


def test_evaluator_threshold_is_backend_decision():
    req = _eval_req("free_text", "energy glucose mitochondria")
    low = E.evaluate_answer(req, threshold=0.99, provider=STUB)
    high = E.evaluate_answer(req, threshold=0.0, provider=STUB)
    assert low.grade == high.grade, "threshold must not change the grade observation"
    assert (low.is_correct, high.is_correct) == (False, True)


def test_evaluator_requires_rubric():
    with pytest.raises(Exception):
        EvaluateRequest(question_text="Q", user_response="A", rubric_chunks=[], question_type="mcq")


# ---------------------------------------------------------------- planner --
def _plan_req(goal="mixed", minutes=20):
    return PlanRequest(
        user_id="u1", document_id="d1",
        user_topic_state=[TopicState(concept="photosynthesis", mastery_score=0.2, confidence=0.8)],
        user_chunk_state=[ChunkState(chunk_id="c1", next_due_at=None, review_count=0),
                          ChunkState(chunk_id="c2", next_due_at=None, review_count=5)],
        chunk_ids=["c1", "c2"],
        chunk_concepts={"c1": ["photosynthesis"], "c2": ["respiration"]},
        available_minutes=minutes, session_goal=goal,
    )


def test_planner_weak_focus_and_capacity():
    out = _tracked(P.plan_session(_plan_req("weak_focus", minutes=10), provider=STUB))
    assert out.recommended_chunk_ids[0] == "c1", "weak-concept chunk first"
    assert out.estimated_duration_minutes <= 10
    assert out.rationale.strip()


def test_planner_validates_unknown_ids():
    plan = P.StudyPlan(recommended_chunk_ids=["c99", "c1", "c1"], estimated_duration_minutes=999,
                       rationale="x", model="t", tokens_used=1)
    fixed = P.validate_plan(plan, _plan_req(minutes=10))
    assert fixed.recommended_chunk_ids == ["c1"]
    assert fixed.estimated_duration_minutes <= 10


# ----------------------------------------------------------------- client --
def test_retry_recovers_and_eventually_raises():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("boom")
        return "ok"

    assert call_with_retry(flaky, max_retries=3, timeout_s=5, label="t") == "ok"

    def dead():
        raise ValueError("always")

    with pytest.raises(RuntimeError):
        call_with_retry(dead, max_retries=2, timeout_s=5, label="t")


def test_timeout_never_blocks():
    def slow():
        time.sleep(10)
        return "late"

    t0 = time.time()
    with pytest.raises(RuntimeError, match="timed out"):
        call_with_retry(slow, max_retries=1, timeout_s=1, label="t")
    assert time.time() - t0 < 8


def test_get_provider_resolution():
    assert get_provider("stub").name == "stub"
    os.environ["AI_ENGINE_PROVIDER"] = "stub"
    try:
        assert get_provider().name == "stub"
    finally:
        del os.environ["AI_ENGINE_PROVIDER"]
