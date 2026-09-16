"""AI hooks for the ingestion pipeline (BUILD_FLOW Phase 2).

- Concepts per chunk: LLM call, stored with confidence.
- Embeddings: batched (50-100/call) with retry + backoff; every record carries
  ``model_name`` and ``model_dim`` so stale-model rows can be flagged later.
- Offline behaviour: when no ``OPENAI_API_KEY`` is set, deterministic stub
  providers are used (zero/hash vectors, no concepts). Stubs are marked and
  must never be mistaken for production embeddings.
"""

from __future__ import annotations

import hashlib
import os
import random
import time

EMBEDDING_MODEL_DEFAULT = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM_DEFAULT = 1536
EMBED_BATCH_SIZE = 64
EMBED_MAX_RETRIES = 4
OCR_MODEL_DEFAULT = os.getenv("OCR_MODEL", "gpt-4o-mini")

Concept = tuple[str, float]  # (normalised label, confidence)


def _openai_client():
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("openai package is required for LLM providers") from e
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    kwargs: dict = {}
    base_url = os.getenv("OPENAI_BASE_URL")  # e.g. https://openrouter.ai/api/v1
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(api_key=api_key, **kwargs)


def _retry_with_backoff(fn, max_retries: int = EMBED_MAX_RETRIES):
    delay = 1.0
    last: Exception | None = None
    for _ in range(max_retries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - retry on any provider error
            last = e
            time.sleep(delay)
            delay *= 2.0
    raise RuntimeError(f"LLM call failed after {max_retries} attempts: {last}") from last


# ------------------------------------------------------------- concepts ---
def extract_concepts(chunk_content: str, model: str | None = None) -> list[Concept]:
    """Extract normalised concept labels for one chunk. Stub when offline."""
    if not os.getenv("OPENAI_API_KEY"):
        return []  # offline stub: no concepts; pipeline still records provenance
    client = _openai_client()

    def _call():
        resp = client.chat.completions.create(
            model=model or os.getenv("CONCEPT_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": "Extract 1-5 short concept labels from the text. Reply with one `label: confidence` per line."},
                {"role": "user", "content": chunk_content[:4000]},
            ],
            timeout=30,
        )
        msg = resp.choices[0].message
        return (getattr(msg, "content", None) or getattr(msg, "reasoning", None) or "")

    raw = _retry_with_backoff(_call)
    concepts: list[Concept] = []
    for line in raw.splitlines():
        if ":" not in line:
            continue
        label, conf = line.split(":", 1)
        label = label.strip().lower()
        try:
            confidence = float(conf.strip())
        except ValueError:
            continue
        if label:
            concepts.append((label, min(1.0, max(0.0, confidence))))
    return concepts


# ------------------------------------------------------------ embeddings ---
def _stub_vector(text: str, dim: int) -> list[float]:
    seed = int(hashlib.sha256(text.encode()).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(dim)]


def embed_texts(
    texts: list[str],
    model: str = EMBEDDING_MODEL_DEFAULT,
    dim: int = EMBEDDING_DIM_DEFAULT,
) -> list[list[float]]:
    """Embed a batch of texts. Returns one vector per input, in order."""
    if not texts:
        return []
    if not os.getenv("OPENAI_API_KEY"):
        return [_stub_vector(t, dim) for t in texts]  # offline stub
    client = _openai_client()
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]

        def _call(b=batch):
            return client.embeddings.create(model=model, input=b, timeout=60)

        resp = _retry_with_backoff(_call)
        vectors.extend([d.embedding for d in resp.data])
    return vectors


# ------------------------------------------------------------ vision OCR ---
def transcribe_page_image(image_png: bytes, page_no: int, model: str | None = None, client=None) -> str:
    """Transcribe a scanned page image via a vision LLM. One call per page.

    Sends the PNG to the chat model and returns raw transcription text (no
    commentary — the prompt forbids it). Requires OPENAI_API_KEY.
    """
    import base64

    client = client or _openai_client()
    data_url = "data:image/png;base64," + base64.b64encode(image_png).decode()

    def _call():
        resp = client.chat.completions.create(
            model=model or OCR_MODEL_DEFAULT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "Transcribe all text visible in this scanned document page. "
                        "Return only the transcribed text, preserving reading order and "
                        "paragraph breaks. No commentary, no markdown fences.")},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            max_tokens=4000,
            timeout=90,
        )
        msg = resp.choices[0].message
        return (getattr(msg, "content", None) or getattr(msg, "reasoning", None) or "")

    return _retry_with_backoff(_call).strip()
