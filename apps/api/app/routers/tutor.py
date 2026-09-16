"""Tutor endpoint (BUILD_FLOW Phase 5 retrieval). Thin over sessions.service."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..routers.documents import get_user_id
from .sessions import get_service_deps

router = APIRouter(prefix="/tutor", tags=["tutor"])


class TutorAskBody(BaseModel):
    document_id: str
    query: str
    conversation_history: list[dict] = []


@router.post("/ask")
def ask(body: TutorAskBody, user_id: str = Depends(get_user_id), deps=Depends(get_service_deps)):
    from ..sessions.service import ask_tutor as _ask

    try:
        return _ask(deps, user_id=user_id, document_id=body.document_id,
                    query=body.query, history=body.conversation_history)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
