"""Question Generator (BUILD_FLOW Phase 3).

Every question is bound to its source chunk (``chunk_id``) and carries
``rubric_chunk_ids`` for the Evaluator. Generation without provenance is
refused — the constructor raises on empty ``chunk_id``/``chunk_content``.
"""

from __future__ import annotations

import json
import re

from .client import CHAT_MODEL_DEFAULT, estimate_tokens, get_provider
from .schemas import GenerateQuestionRequest, GeneratedQuestion, MCQOption

_SYSTEM = (
    "You generate study questions from a single source chunk. Reply with JSON only: "
    '{"question_text": str, "options": [{"id": "A"|"B"|"C"|"D", "text": str}] | null, '
    '"correct_option_id": str | null, "rubric_note": str}. '
    "For mcq: exactly 4 options with exactly one correct. "
    "For true_false: options null, correct_option_id 'true' or 'false'. "
    "For free_text: options and correct_option_id null."
)


def generate_question(req: GenerateQuestionRequest, provider=None, model: str = CHAT_MODEL_DEFAULT) -> GeneratedQuestion:
    if not req.chunk_id or not req.chunk_content.strip():
        raise ValueError("question generation requires a non-empty chunk_id and chunk_content")
    prov = get_provider(provider)
    if prov.name == "stub":
        return _stub_generate(req)
    concepts = ", ".join(req.concepts) or "general understanding"
    user = (
        f"Section: {req.section_heading or 'untitled'}\nConcepts: {concepts}\n"
        f"Type: {req.question_type}\nChunk:\n{req.chunk_content[:4000]}"
    )
    text, tokens = prov.chat(system=_SYSTEM, user=user, model=model, timeout_s=30, max_tokens=1500)
    return _parse_llm_output(req, text, model, tokens)


def _parse_llm_output(req: GenerateQuestionRequest, text: str, model: str, tokens: int) -> GeneratedQuestion:
    try:
        data = json.loads(re.search(r"\{.*\}", text, re.DOTALL).group(0))  # type: ignore[union-attr]
    except Exception as e:
        raise ValueError(f"generator returned unparseable output: {e}") from e
    options = None
    if req.question_type == "mcq":
        raw_opts = data.get("options") or []
        if len(raw_opts) != 4:
            raise ValueError("mcq generation must return exactly 4 options")
        options = [MCQOption(id=o["id"], text=o["text"]) for o in raw_opts]
        if data.get("correct_option_id") not in {o.id for o in options}:
            raise ValueError("mcq correct_option_id must match one option")
    return GeneratedQuestion(
        question_text=data["question_text"],
        question_type=req.question_type,
        options=options,
        correct_option_id=data.get("correct_option_id"),
        rubric_chunk_ids=[req.chunk_id],
        model=model,
        tokens_used=tokens,
    )


def _first_sentences(text: str, n: int) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]
    return parts[:n]


def _stub_generate(req: GenerateQuestionRequest) -> GeneratedQuestion:
    sents = _first_sentences(req.chunk_content, 4)
    stem = sents[0] if sents else req.chunk_content[:200]
    heading = req.section_heading or "the text"
    if req.question_type == "mcq":
        distractors = (sents[1:4] + [
            "The text makes no such claim.",
            "This contradicts the text.",
            "The text does not discuss this.",
        ])[:3]
        options = [MCQOption(id="A", text=stem)] + [
            MCQOption(id=i, text=d) for i, d in zip(("B", "C", "D"), distractors)
        ]
        q = f"According to {heading}, which statement is supported by the text?"
        return GeneratedQuestion(question_text=q, question_type="mcq", options=options,
                                 correct_option_id="A", rubric_chunk_ids=[req.chunk_id],
                                 model="stub-qgen-v1", tokens_used=estimate_tokens(q) + estimate_tokens(stem))
    if req.question_type == "true_false":
        q = f"True or false: {stem}"
        return GeneratedQuestion(question_text=q, question_type="true_false", options=None,
                                 correct_option_id="true", rubric_chunk_ids=[req.chunk_id],
                                 model="stub-qgen-v1", tokens_used=estimate_tokens(q))
    q = f"Summarise the key point of this passage in your own words: {stem}"
    return GeneratedQuestion(question_text=q, question_type="free_text", options=None,
                             correct_option_id=None, rubric_chunk_ids=[req.chunk_id],
                             model="stub-qgen-v1", tokens_used=estimate_tokens(q))
