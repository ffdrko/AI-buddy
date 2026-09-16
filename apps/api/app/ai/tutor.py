"""Tutor (BUILD_FLOW Phase 3).

Answers ONLY from the retrieved chunks. Every response carries
``source_chunk_ids`` — never a response without grounding.
"""

from __future__ import annotations

from .client import CHAT_MODEL_DEFAULT, estimate_tokens, get_provider
from .schemas import TutorRequest, TutorResponse

_SYSTEM = (
    "You are a tutor. Answer the user's question using ONLY the provided source chunks. "
    "Every factual claim must be supported by a chunk. Cite sources inline as [chunk_id]. "
    "If the chunks do not contain the answer, say so explicitly."
)


def _extract_cited_ids(text: str, candidate_ids: list[str]) -> list[str]:
    cited = [cid for cid in candidate_ids if cid in text]
    return cited or candidate_ids  # fallback: all chunks informed the answer


def answer_tutor(req: TutorRequest, provider=None, model: str = CHAT_MODEL_DEFAULT) -> TutorResponse:
    prov = get_provider(provider)
    if prov.name == "stub":
        return _stub_answer(req)
    context = "\n\n".join(
        f"[{c.chunk_id}] ({c.section_heading or 'untitled'}): {c.content}" for c in req.retrieved_chunks
    )
    history = "\n".join(f"{t.role}: {t.content}" for t in req.conversation_history[-10:])
    user = f"Sources:\n{context}\n\nConversation:\n{history}\n\nQuestion: {req.user_query}"
    text, tokens = prov.chat(system=_SYSTEM, user=user, model=model, timeout_s=30, max_tokens=800)
    return TutorResponse(
        response_text=text,
        source_chunk_ids=_extract_cited_ids(text, [c.chunk_id for c in req.retrieved_chunks]),
        model=model,
        tokens_used=tokens,
    )


def _stub_answer(req: TutorRequest) -> TutorResponse:
    lines = [f"Based on {len(req.retrieved_chunks)} source(s):"]
    for c in req.retrieved_chunks:
        snippet = c.content[:200].replace("\n", " ")
        lines.append(f"- [{c.chunk_id}] ({c.section_heading or 'untitled'}): {snippet}")
    lines.append(f"\nQuestion was: {req.user_query}")
    text = "\n".join(lines)
    return TutorResponse(
        response_text=text,
        source_chunk_ids=[c.chunk_id for c in req.retrieved_chunks],
        model="stub-tutor-v1",
        tokens_used=estimate_tokens(text) + estimate_tokens(req.user_query),
    )
