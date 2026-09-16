"""Ingestion orchestrator (BUILD_FLOW Phase 2).

Flow: validate -> store raw -> hash/dedupe -> extract -> clean -> chunk ->
concepts -> embed -> persist -> ready. Any step failure -> failed + message,
with partial artifacts from this run removed (never leave partial state).

Persistence goes through the ``DocumentStore`` protocol so the orchestrator
is testable without a live DB. ``SqlAlchemyDocumentStore`` is the real
implementation over Phase 1 models.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .chunk import Chunk, chunk_text
from .clean import clean_text
from .llm import (
    EMBEDDING_DIM_DEFAULT,
    EMBEDDING_MODEL_DEFAULT,
    Concept,
    embed_texts,
    extract_concepts,
)
from .pdf_extract import extract_pdf_text

logger = logging.getLogger("api.ingestion")

MAX_UPLOAD_MB = 50
MAX_PAGES = 2000
PDF_MAGIC = b"%PDF"

TERMINAL_STATUSES = ("ready", "failed")
PIPELINE_STAGES = ("pending", "extracting", "chunking", "embedding")


def compute_content_hash(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def validate_upload(filename: str, pdf_bytes: bytes, max_mb: int = MAX_UPLOAD_MB) -> None:
    if not filename.lower().endswith(".pdf"):
        raise ValueError("only .pdf uploads are accepted")
    if len(pdf_bytes) > max_mb * 1024 * 1024:
        raise ValueError(f"file exceeds {max_mb} MB limit")
    if not pdf_bytes.startswith(PDF_MAGIC):
        raise ValueError("not a valid PDF (bad magic bytes)")


# ------------------------------------------------------------ store -----
class DocumentStore(Protocol):
    def find_by_hash(self, content_hash: str) -> dict | None: ...
    def get(self, document_id: str) -> dict | None: ...
    def create_pending(self, *, user_id: str, title: str, filename: str, storage_key: str, content_hash: str, page_count: int | None) -> dict: ...
    def set_status(self, document_id: str, status: str, *, error: str | None = None, patch: dict | None = None) -> None: ...
    def add_chunks(self, document_id: str, chunks: list[Chunk], page_map: dict[int, tuple[int | None, int | None]]) -> list[str]: ...
    def add_embeddings(self, chunk_ids: list[str], vectors: list[list[float]], model_name: str, model_dim: int) -> None: ...
    def add_concepts(self, chunk_ids: list[str], concepts: list[list[Concept]]) -> None: ...
    def delete_run_artifacts(self, document_id: str) -> None: ...
    def delete_document(self, document_id: str) -> None: ...


@dataclass
class PipelineDeps:
    store: DocumentStore
    storage_put: Any  # (key, bytes, content_type) -> key
    embed_fn: Any = embed_texts
    concept_fn: Any = extract_concepts
    ocr_fn: Any = None
    embedding_model: str = EMBEDDING_MODEL_DEFAULT
    embedding_dim: int = EMBEDDING_DIM_DEFAULT


@dataclass
class IngestionResult:
    document_id: str
    status: str
    chunks: int = 0
    deduped: bool = False
    error: str | None = None


def run_upload(
    *,
    user_id: str,
    filename: str,
    pdf_bytes: bytes,
    title: str | None,
    deps: PipelineDeps,
    storage_key_fn=None,
) -> IngestionResult:
    """Validate, dedupe, create the pending row. Worker runs ``run_ingestion`` next."""
    from ..storage import raw_pdf_key

    validate_upload(filename, pdf_bytes)
    content_hash = compute_content_hash(pdf_bytes)
    existing = deps.store.find_by_hash(content_hash)
    if existing is not None:
        if existing.get("status") == "failed":
            # A failed row must never block a retry: drop it and start over.
            logger.info("retry after failure hash=%s doc=%s", content_hash[:12], existing["id"])
            deps.store.delete_document(str(existing["id"]))
        else:
            logger.info("dedupe hit hash=%s doc=%s", content_hash[:12], existing["id"])
            return IngestionResult(document_id=str(existing["id"]), status=existing.get("status", "ready"), deduped=True)
    key = (storage_key_fn or raw_pdf_key)(content_hash)
    deps.storage_put(key, pdf_bytes, "application/pdf")
    doc = deps.store.create_pending(
        user_id=user_id,
        title=title or filename,
        filename=filename,
        storage_key=key,
        content_hash=content_hash,
        page_count=None,
    )
    return IngestionResult(document_id=str(doc["id"]), status="pending")


def run_ingestion(document_id: str, pdf_bytes: bytes, deps: PipelineDeps) -> IngestionResult:
    """Run the worker-side pipeline synchronously. Never raises; records failure."""
    store = deps.store
    try:
        store.set_status(document_id, "extracting")
        full_text, page_count, pages, used_ocr = extract_pdf_text(pdf_bytes, ocr_fn=deps.ocr_fn)
        if page_count > MAX_PAGES:
            raise ValueError(f"PDF has {page_count} pages; limit is {MAX_PAGES}")
        if not full_text.strip():
            raise ValueError("no extractable text found in PDF")
        store.set_status(document_id, "extracting", patch={"page_count": page_count})
        _persist_text_copy(document_id, full_text, deps)

        store.set_status(document_id, "chunking")
        cleaned = clean_text(full_text)
        chunks = chunk_text(cleaned)
        if not chunks:
            raise ValueError("chunking produced no chunks")
        page_map = _map_chunks_to_pages(chunks, pages)
        chunk_ids = store.add_chunks(document_id, chunks, page_map)

        store.set_status(document_id, "embedding")
        vectors = deps.embed_fn([c.content for c in chunks], deps.embedding_model, deps.embedding_dim)
        if len(vectors) != len(chunk_ids):
            raise ValueError(f"embedder returned {len(vectors)} vectors for {len(chunk_ids)} chunks")
        store.add_embeddings(chunk_ids, vectors, deps.embedding_model, deps.embedding_dim)

        per_chunk_concepts = [deps.concept_fn(c.content) for c in chunks]
        store.add_concepts(chunk_ids, per_chunk_concepts)

        store.set_status(document_id, "ready")
        logger.info("ingestion ready doc=%s chunks=%d ocr=%s", document_id, len(chunk_ids), used_ocr)
        return IngestionResult(document_id=document_id, status="ready", chunks=len(chunk_ids))
    except Exception as e:  # noqa: BLE001 - record any failure on the row
        logger.error("ingestion failed doc=%s err=%s", document_id, e, exc_info=True)
        try:
            store.delete_run_artifacts(document_id)
        finally:
            store.set_status(document_id, "failed", error=str(e))
        return IngestionResult(document_id=document_id, status="failed", error=str(e))


def _persist_text_copy(document_id: str, full_text: str, deps: PipelineDeps) -> None:
    from ..storage import raw_text_key

    key = raw_text_key(document_id)
    try:
        deps.storage_put(key, full_text.encode("utf-8"), "text/plain")
        deps.store.set_status(document_id, "extracting", patch={"raw_text_key": key})
    except Exception as e:
        logger.warning("raw text preservation failed doc=%s err=%s", document_id, e)


def _map_chunks_to_pages(chunks: list[Chunk], pages) -> dict[int, tuple[int | None, int | None]]:
    """Best-effort: locate each chunk's content in the concatenated page texts."""
    page_map: dict[int, tuple[int | None, int | None]] = {}
    for i, ch in enumerate(chunks):
        probe = ch.content[:120].strip()
        found = None
        for p in pages:
            if probe and probe[:60] in p.text:
                found = (p.page_no, p.page_no)
                break
        page_map[i] = found or (None, None)
    return page_map


# NOTE: implemented with a local import so `app.ingestion` stays importable
# without the db package on sys.path (tests use InMemoryDocumentStore).
def _models():
    from ..dbpath import ensure_db_path

    ensure_db_path()
    from db import models as m

    return m


@dataclass
class SADocumentStore:
    session_factory: Any

    def find_by_hash(self, content_hash: str) -> dict | None:
        m = _models()
        with self.session_factory() as s:
            doc = s.query(m.Document).filter(m.Document.content_hash == content_hash).first()
            if doc is None or getattr(doc, "deleted_at", None):
                return None
            return {"id": str(doc.id), "status": doc.status}

    def get(self, document_id: str) -> dict | None:
        m = _models()
        with self.session_factory() as s:
            doc = s.query(m.Document).filter(m.Document.id == document_id).first()
            if doc is None or getattr(doc, "deleted_at", None):
                return None
            return {
                "id": str(doc.id), "user_id": str(doc.user_id), "title": doc.title,
                "original_filename": doc.original_filename, "storage_key": doc.storage_key,
                "raw_text_key": doc.raw_text_key, "content_hash": doc.content_hash,
                "page_count": doc.page_count, "status": doc.status,
                "error_message": doc.error_message, "created_at": doc.created_at.isoformat() if doc.created_at else None,
            }

    def create_pending(self, *, user_id: str, title: str, filename: str, storage_key: str, content_hash: str, page_count: int | None) -> dict:
        m = _models()
        with self.session_factory() as s:
            doc = m.Document(user_id=user_id, title=title, original_filename=filename,
                             storage_key=storage_key, content_hash=content_hash,
                             page_count=page_count, status="pending")
            s.add(doc)
            s.commit()
            s.refresh(doc)
            return {"id": str(doc.id), "status": doc.status}

    def set_status(self, document_id: str, status: str, *, error: str | None = None, patch: dict | None = None) -> None:
        m = _models()
        with self.session_factory() as s:
            doc = s.query(m.Document).filter(m.Document.id == document_id).first()
            if doc is None:
                return
            doc.status = status
            doc.error_message = error
            for k, v in (patch or {}).items():
                setattr(doc, k, v)
            s.commit()

    def add_chunks(self, document_id: str, chunks: list[Chunk], page_map: dict[int, tuple[int | None, int | None]]) -> list[str]:
        m = _models()
        ids: list[str] = []
        with self.session_factory() as s:
            for i, ch in enumerate(chunks):
                ps, pe = page_map.get(i, (None, None))
                row = m.DocumentChunk(document_id=document_id, chunk_index=i, content=ch.content,
                                      page_start=ps, page_end=pe,
                                      section_heading=ch.section_heading, token_count=ch.token_count)
                s.add(row)
                s.flush()
                ids.append(str(row.id))
            s.commit()
        return ids

    def add_embeddings(self, chunk_ids: list[str], vectors: list[list[float]], model_name: str, model_dim: int) -> None:
        m = _models()
        with self.session_factory() as s:
            for cid, vec in zip(chunk_ids, vectors):
                s.add(m.Embedding(chunk_id=cid, model_name=model_name, model_dim=model_dim, vector=vec))
            s.commit()

    def add_concepts(self, chunk_ids: list[str], concepts: list[list[Concept]]) -> None:
        m = _models()
        with self.session_factory() as s:
            for cid, clist in zip(chunk_ids, concepts):
                for label, conf in clist:
                    s.add(m.ChunkConcept(chunk_id=cid, concept=label, confidence=conf))
            s.commit()

    def delete_run_artifacts(self, document_id: str) -> None:
        m = _models()
        with self.session_factory() as s:
            chunk_ids = [r[0] for r in s.query(m.DocumentChunk.id).filter(m.DocumentChunk.document_id == document_id).all()]
            if chunk_ids:
                s.query(m.Embedding).filter(m.Embedding.chunk_id.in_(chunk_ids)).delete(synchronize_session=False)
                s.query(m.ChunkConcept).filter(m.ChunkConcept.chunk_id.in_(chunk_ids)).delete(synchronize_session=False)
                s.query(m.DocumentChunk).filter(m.DocumentChunk.id.in_(chunk_ids)).delete(synchronize_session=False)
            s.commit()

    def list_for_user(self, user_id: str) -> list[dict]:
        m = _models()
        with self.session_factory() as s:
            q = s.query(m.Document).filter(m.Document.user_id == user_id)
            if hasattr(m.Document, "deleted_at"):
                q = q.filter(m.Document.deleted_at.is_(None))
            return [
                {"id": str(d.id), "title": d.title, "status": d.status,
                 "page_count": d.page_count,
                 "created_at": d.created_at.isoformat() if d.created_at else None}
                for d in q.order_by(m.Document.created_at.desc()).all()
            ]

    def soft_delete(self, document_id: str) -> None:
        from datetime import datetime, timezone

        m = _models()
        with self.session_factory() as s:
            doc = s.query(m.Document).filter(m.Document.id == document_id).first()
            if doc is None:
                return
            if hasattr(doc, "deleted_at"):
                doc.deleted_at = datetime.now(timezone.utc)
            else:  # pre-0002 schema fallback: hard delete
                s.delete(doc)
            s.commit()

    def delete_document(self, document_id: str) -> None:
        """Hard delete a document and its artifacts (failed-retry path)."""
        m = _models()
        with self.session_factory() as s:
            self.delete_run_artifacts(document_id)
            # Re-query in this session: delete_run_artifacts uses its own session.
            doc = s.query(m.Document).filter(m.Document.id == document_id).first()
            if doc is not None:
                s.delete(doc)
            s.commit()
