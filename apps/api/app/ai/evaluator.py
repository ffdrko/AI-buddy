"""Evaluator (BUILD_FLOW Phase 3).

Grades a response against rubric chunks. The grade is an OBSERVATION —
the Learning Engine decides what to do with it. ``is_correct`` applies the
backend-supplied threshold (default 0.6); the LLM never sets policy.
"""

from __future__ import annotations

import json
import re

from .client import CHAT_MODEL_DEFAULT, estimate_tokens, get_provider
from .schemas import EvaluateRequest, Evaluation

_SYSTEM = (
    "You grade a student's answer against the provided rubric chunks. Reply with JSON only: "
    '{"grade": 0.0-1.0, "feedback": str}. The feedback must cite the rubric. '
    "Be strict but fair; partial credit for partially correct answers."
)

_STOPWORDS = frozenset(
    "the a an of to in on for and or is are was were be been with that this these those it its as at by from will would can should have has had not no do does did".split()
)


def _keywords(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z]{5,}", text)} - _STOPWORDS


def evaluate_answer(req: EvaluateRequest, threshold: float = 0.6, provider=None, model: str = CHAT_MODEL_DEFAULT) -> Evaluation:
    if not req.rubric_chunks:
        raise ValueError("evaluation requires at least one rubric chunk")
    prov = get_provider(provider)
    if prov.name == "stub":
        return _stub_evaluate(req, threshold)
    rubric = "\n\n".join(f"[{c.chunk_id}]: {c.content}" for c in req.rubric_chunks)
    user = (
        f"Question ({req.question_type}): {req.question_text}\n"
        f"Student answer: {req.user_response}\n"
        f"Selected option: {req.selected_option_id}\nCorrect option: {req.correct_option_id}\n"
        f"Rubric:\n{rubric[:6000]}"
    )
    text, tokens = prov.chat(system=_SYSTEM, user=user, model=model, timeout_s=30, max_tokens=500)
    try:
        data = json.loads(re.search(r"\{.*\}", text, re.DOTALL).group(0))  # type: ignore[union-attr]
        grade = min(1.0, max(0.0, float(data["grade"])))
        feedback = str(data.get("feedback", ""))
    except Exception as e:
        raise ValueError(f"evaluator returned unparseable output: {e}") from e
    return Evaluation(grade=grade, is_correct=grade >= threshold, feedback=feedback, model=model, tokens_used=tokens)


def _stub_evaluate(req: EvaluateRequest, threshold: float) -> Evaluation:
    cited = ", ".join(c.chunk_id for c in req.rubric_chunks)
    if req.question_type in ("mcq", "true_false"):
        if req.correct_option_id is None or req.selected_option_id is None:
            grade, feedback = 0.0, f"No option recorded; cannot verify against rubric [{cited}]."
        elif req.selected_option_id == req.correct_option_id:
            grade, feedback = 1.0, f"Correct option selected, consistent with rubric [{cited}]."
        else:
            grade, feedback = 0.0, f"Selected option does not match the correct answer per rubric [{cited}]."
        return Evaluation(grade=grade, is_correct=grade >= threshold, feedback=feedback,
                          model="stub-eval-v1", tokens_used=estimate_tokens(req.user_response))
    rubric_text = " ".join(c.content for c in req.rubric_chunks)
    keys = _keywords(rubric_text)
    if not keys:
        return Evaluation(grade=0.0, is_correct=False, feedback="Rubric has no gradeable keywords.",
                          model="stub-eval-v1", tokens_used=estimate_tokens(req.user_response))
    hit = keys & _keywords(req.user_response)
    grade = round(len(hit) / len(keys), 3)
    feedback = (
        f"Matched {len(hit)}/{len(keys)} key terms from rubric [{cited}]. "
        + ("Good coverage." if grade >= threshold else "Missing key ideas; review the cited sources.")
    )
    return Evaluation(grade=grade, is_correct=grade >= threshold, feedback=feedback,
                      model="stub-eval-v1", tokens_used=estimate_tokens(req.user_response) + estimate_tokens(rubric_text))
