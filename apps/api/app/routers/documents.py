"""Document endpoints. JWT-guarded; documents scoped to the calling user."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from ..auth import require_user

router = APIRouter(prefix="/documents", tags=["documents"])


@lru_cache(maxsize=1)
def _storage():
    from ..storage import build_storage_from_env

    return build_storage_from_env()


def get_store():
    from ..dbpath import ensure_db_path
    from ..ingestion.pipeline import SADocumentStore
    from ..storage import build_storage_from_env  # noqa: F401

    ensure_db_path()
    from db.session import get_engine
    from sqlalchemy.orm import sessionmaker

    engine = get_engine()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return SADocumentStore(session_factory=factory)


def get_pipeline_deps(store=Depends(get_store)):
    from ..config import settings
    from ..ingestion.llm import EMBEDDING_DIM_DEFAULT, EMBEDDING_MODEL_DEFAULT
    from ..ingestion.pdf_extract import resolve_ocr_fn
    from ..ingestion.pipeline import PipelineDeps

    return PipelineDeps(
        store=store,
        storage_put=_storage().put_bytes,
        ocr_fn=resolve_ocr_fn(getattr(settings, "ocr_provider", "auto")),
        embedding_model=getattr(settings, "embedding_model", EMBEDDING_MODEL_DEFAULT),
        embedding_dim=getattr(settings, "embedding_dim", EMBEDDING_DIM_DEFAULT),
    )


@router.post("/upload", status_code=201)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    user_id: str = Depends(require_user),
    deps=Depends(get_pipeline_deps),
):
    from ..ingestion.pipeline import run_ingestion, run_upload

    pdf_bytes = await file.read()
    try:
        result = run_upload(
            user_id=user_id, filename=file.filename or "upload.pdf",
            pdf_bytes=pdf_bytes, title=title, deps=deps,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result.deduped:
        return {"document_id": result.document_id, "status": result.status, "deduped": True}
    background.add_task(run_ingestion, result.document_id, pdf_bytes, deps)
    return {"document_id": result.document_id, "status": result.status, "deduped": False}


@router.get("/{document_id}/status")
def document_status(document_id: str, user_id: str = Depends(require_user), store=Depends(get_store)):
    doc = store.get(document_id)
    if doc is None or doc["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="document not found")
    position = None
    if doc["status"] == "pending":
        from ..worker import queue_position

        position = queue_position(document_id)
    return {"document_id": document_id, "status": doc["status"],
            "error_message": doc.get("error_message"), "queue_position": position}


@router.get("/{document_id}")
def get_document(document_id: str, user_id: str = Depends(require_user), store=Depends(get_store)):
    doc = store.get(document_id)
    if doc is None or doc["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="document not found")
    return doc


@router.get("")
def list_documents(user_id: str = Depends(require_user), store=Depends(get_store)):
    return {"documents": store.list_for_user(user_id)}


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: str, user_id: str = Depends(require_user), store=Depends(get_store)):
    doc = store.get(document_id)
    if doc is None or doc["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="document not found")
    store.soft_delete(document_id)
    return None
