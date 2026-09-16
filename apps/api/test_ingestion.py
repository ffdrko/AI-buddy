"""Phase 2 gate tests (offline-capable).

Covers: validation, dedupe (same PDF -> same document_id, no re-run),
extract->clean->chunk->embed->concepts->ready happy path, failure path
(failed + error_message, no partial state), scanned-without-OCR failure,
embedding model version recorded, chunking targets/overlap.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
sys.path.insert(0, os.path.dirname(__file__))

from app.ingestion.chunk import chunk_text
from app.ingestion.clean import clean_text
from app.ingestion.llm import EMBEDDING_DIM_DEFAULT, EMBEDDING_MODEL_DEFAULT
from app.ingestion.pipeline import (
    PipelineDeps,
    compute_content_hash,
    run_ingestion,
    run_upload,
    validate_upload,
)


def _words(text: str) -> int:
    return len(text.split())


# ------------------------------------------------- in-memory store -----
class InMemoryDocumentStore:
    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.by_hash: dict[str, str] = {}
        self.chunks: dict[str, list[dict]] = {}
        self.embeddings: dict[str, dict] = {}
        self.concepts: dict[str, list] = {}
        self.creates = 0
        self._seq = 0

    def _nid(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    # -- DocumentStore protocol --
    def find_by_hash(self, content_hash: str):
        doc_id = self.by_hash.get(content_hash)
        return self.docs.get(doc_id) if doc_id else None

    def get(self, document_id: str):
        return self.docs.get(document_id)

    def create_pending(self, *, user_id, title, filename, storage_key, content_hash, page_count):
        self.creates += 1
        doc_id = self._nid("doc")
        doc = {"id": doc_id, "user_id": user_id, "title": title, "status": "pending",
               "storage_key": storage_key, "content_hash": content_hash,
               "page_count": page_count, "error_message": None}
        self.docs[doc_id] = doc
        self.by_hash[content_hash] = doc_id
        return {"id": doc_id, "status": "pending"}

    def set_status(self, document_id, status, *, error=None, patch=None):
        self.docs[document_id]["status"] = status
        self.docs[document_id]["error_message"] = error
        for k, v in (patch or {}).items():
            self.docs[document_id][k] = v

    def add_chunks(self, document_id, chunks, page_map):
        ids = []
        for i, ch in enumerate(chunks):
            cid = self._nid("chunk")
            self.chunks.setdefault(document_id, []).append({"id": cid, "content": ch.content})
            ids.append(cid)
        return ids

    def add_embeddings(self, chunk_ids, vectors, model_name, model_dim):
        for cid, vec in zip(chunk_ids, vectors):
            self.embeddings[cid] = {"vector": vec, "model_name": model_name, "model_dim": model_dim}

    def add_concepts(self, chunk_ids, concepts):
        for cid, clist in zip(chunk_ids, concepts):
            self.concepts[cid] = clist

    def delete_run_artifacts(self, document_id):
        for c in self.chunks.pop(document_id, []):
            self.embeddings.pop(c["id"], None)
            self.concepts.pop(c["id"], None)

    def delete_document(self, document_id):
        self.delete_run_artifacts(document_id)
        doc = self.docs.pop(document_id, None)
        if doc is not None:
            self.by_hash.pop(doc["content_hash"], None)


def _deps(store, **kw):
    stored: dict[str, bytes] = {}
    kw.setdefault("embed_fn", lambda texts, model, dim: [[0.0] * dim for _ in texts])
    kw.setdefault("concept_fn", lambda content: [("test-concept", 0.9)])
    return PipelineDeps(store=store, storage_put=lambda k, b, ct="": stored.__setitem__(k, b) or k, **kw)


def _make_pdf(pages: list[str]) -> bytes:
    fitz = __import__("fitz")
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    out = doc.tobytes()
    doc.close()
    return out


# ---------------------------------------------------------------- tests ---
def test_hash_stable():
    assert compute_content_hash(b"%PDF-abc") == compute_content_hash(b"%PDF-abc")
    assert compute_content_hash(b"%PDF-a") != compute_content_hash(b"%PDF-b")


def test_validate_rejects_non_pdf():
    try:
        validate_upload("notes.txt", b"hello")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for non-pdf")
    try:
        validate_upload("x.pdf", b"not a pdf at all")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for bad magic")


def test_clean():
    assert "system" in clean_text("sys-\ntem")  # dehyphenate
    assert "fi" in clean_text("ﬁle")  # ligature
    assert clean_text("a   b\n\n\nc") == "a b\n\nc"
    assert clean_text(clean_text("a  b")) == clean_text("a  b")  # idempotent


def test_chunk_headings_and_overlap():
    text = "# Intro\n" + ("word " * 400 + "\n") + "# Next\n" + ("other " * 100 + "\n")
    chunks = chunk_text(clean_text(text), target_tokens=350, overlap_tokens=50)
    assert len(chunks) >= 2
    headings = {c.section_heading for c in chunks}
    assert "Intro" in headings and "Next" in headings
    for c in chunks:
        assert c.token_count > 0 and c.content.strip()


def test_upload_dedupe_same_pdf_same_id():
    store = InMemoryDocumentStore()
    pdf = _make_pdf(["hello world"])
    r1 = run_upload(user_id="u1", filename="a.pdf", pdf_bytes=pdf, title=None, deps=_deps(store))
    r2 = run_upload(user_id="u1", filename="a.pdf", pdf_bytes=pdf, title=None, deps=_deps(store))
    assert r1.document_id == r2.document_id
    assert r2.deduped is True
    assert store.creates == 1, "pipeline must not re-run on duplicate upload"


def test_failed_upload_can_be_retried():
    store = InMemoryDocumentStore()
    pdf = _make_pdf(["hello world"])
    r1 = run_upload(user_id="u1", filename="a.pdf", pdf_bytes=pdf, title=None, deps=_deps(store))
    store.set_status(r1.document_id, "failed", error="tesseract is not installed")
    r2 = run_upload(user_id="u1", filename="a.pdf", pdf_bytes=pdf, title=None, deps=_deps(store))
    assert r2.deduped is False, "failed rows must not block retry"
    assert r2.document_id != r1.document_id
    assert r2.status == "pending"
    assert store.creates == 2
    assert r1.document_id not in store.docs


def test_full_pipeline_ready():
    store = InMemoryDocumentStore()
    pdf = _make_pdf(["# Study Guide\n" + "photosynthesis " * 200, "Second page " + "chlorophyll " * 100])
    up = run_upload(user_id="u1", filename="g.pdf", pdf_bytes=pdf, title="Guide", deps=_deps(store))
    res = run_ingestion(up.document_id, pdf, _deps(store))
    assert res.status == "ready", res.error
    assert store.docs[up.document_id]["status"] == "ready"
    assert store.docs[up.document_id]["page_count"] == 2
    chunk_ids = [c["id"] for c in store.chunks[up.document_id]]
    assert len(chunk_ids) >= 1
    # chunk -> embedding -> concept rows all present and linked
    for cid in chunk_ids:
        emb = store.embeddings[cid]
        assert emb["model_name"] == EMBEDDING_MODEL_DEFAULT
        assert emb["model_dim"] == EMBEDDING_DIM_DEFAULT
        assert len(emb["vector"]) == EMBEDDING_DIM_DEFAULT
        assert store.concepts[cid] == [("test-concept", 0.9)]


def test_failure_leaves_no_partial_state():
    store = InMemoryDocumentStore()
    pdf = _make_pdf(["real content " * 50])

    def _boom(texts, model, dim):
        raise RuntimeError("embedder exploded")

    up = run_upload(user_id="u1", filename="ok.pdf", pdf_bytes=pdf, title=None, deps=_deps(store))
    res = run_ingestion(up.document_id, pdf, _deps(store, embed_fn=_boom))
    assert res.status == "failed"
    assert res.error, "failure must carry a useful error message"
    assert store.docs[up.document_id]["status"] == "failed"
    assert store.docs[up.document_id]["error_message"]
    assert store.chunks.get(up.document_id) in (None, []), "no partial chunks on failure"
    assert not store.embeddings and not store.concepts


def test_scanned_page_without_ocr_fails_cleanly():
    store = InMemoryDocumentStore()
    up = run_upload(user_id="u1", filename="scan.pdf", pdf_bytes=_make_pdf([""]), title=None, deps=_deps(store))
    res = run_ingestion(up.document_id, _make_pdf([""]), _deps(store))
    assert res.status == "failed"
    assert "OCR" in (res.error or "") or "scanned" in (res.error or "")
    assert store.chunks.get(up.document_id) in (None, []), "no partial chunks on failure"
    assert store.docs[up.document_id]["status"] == "failed"


def test_scanned_page_with_ocr_succeeds():
    from app.ingestion.pdf_extract import default_ocr_fn, resolve_ocr_fn

    store = InMemoryDocumentStore()
    fake_ocr = lambda png, page_no: "transcribed scan content " * 60  # noqa: E731
    up = run_upload(user_id="u1", filename="scan.pdf", pdf_bytes=_make_pdf([""]), title=None, deps=_deps(store))
    res = run_ingestion(up.document_id, _make_pdf([""]), _deps(store, ocr_fn=fake_ocr))
    assert res.status == "ready", res.error
    assert store.docs[up.document_id]["status"] == "ready"

    assert resolve_ocr_fn("tesseract") is default_ocr_fn


def test_ocr_provider_selection(monkeypatch):
    import os

    from app.ingestion import pdf_extract as P
    from app.ingestion.llm import transcribe_page_image

    assert P.resolve_ocr_fn("llm") is transcribe_page_image
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fn = P.resolve_ocr_fn("auto")
    try:
        fn(b"img", 1)
    except RuntimeError as e:
        assert "no OCR is available" in str(e)
    else:
        raise AssertionError("expected helpful OCR error")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(P, "tesseract_available", lambda: False)
    assert P.resolve_ocr_fn("auto") is transcribe_page_image
    monkeypatch.setattr(P, "tesseract_available", lambda: True)
    assert P.resolve_ocr_fn("auto") is P.default_ocr_fn


def test_transcribe_page_image_with_fake_client():
    from app.ingestion.llm import transcribe_page_image

    class FakeMsg:
        content = "  hello scan  "

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kw):
            assert kw["messages"][0]["content"][1]["type"] == "image_url"
            assert kw["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
            return FakeResp()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    assert transcribe_page_image(b"fakepng", 1, client=FakeClient()) == "hello scan"
