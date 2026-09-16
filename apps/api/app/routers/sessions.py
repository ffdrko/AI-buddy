"""Session endpoints (BUILD_FLOW Phase 5). Thin over sessions.service."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from ..routers.documents import get_user_id

router = APIRouter(prefix="/sessions", tags=["sessions"])


class StartSessionBody(BaseModel):
    document_id: str
    available_minutes: int = 25
    session_goal: str = "mixed"


class AnswerBody(BaseModel):
    question_id: str
    response_text: str | None = None
    selected_option_id: str | None = None
    time_seconds: int | None = None
    hints_used: int = 0


def get_session_store():
    import os
    import sys

    from sqlalchemy.orm import sessionmaker

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "db", "src"))
    from db.session import get_engine

    from ..sessions.store import SASessionStore

    return SASessionStore(session_factory=sessionmaker(bind=get_engine(), autoflush=False, autocommit=False))


def get_service_deps(store=Depends(get_session_store)):
    from ..config import settings
    from ..sessions.service import ServiceDeps

    return ServiceDeps(store=store, embedding_model=settings.embedding_model, embedding_dim=settings.embedding_dim)


def _translate(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/start", status_code=201)
def start_session(body: StartSessionBody, background: BackgroundTasks,
                  user_id: str = Depends(get_user_id), deps=Depends(get_service_deps)):
    from ..sessions.service import pregenerate_questions, start_session as _start

    result = _translate(_start, deps, user_id=user_id, document_id=body.document_id,
                        available_minutes=body.available_minutes, session_goal=body.session_goal)
    background.add_task(pregenerate_questions, deps, result["session_id"], result["recommended_chunk_ids"])
    return result


@router.get("/{session_id}/next-question")
def next_question(session_id: str, user_id: str = Depends(get_user_id), deps=Depends(get_service_deps)):
    from ..sessions.service import get_next_question as _next

    return _translate(_next, deps, user_id=user_id, session_id=session_id)


@router.post("/{session_id}/answer")
def answer(session_id: str, body: AnswerBody,
           user_id: str = Depends(get_user_id), deps=Depends(get_service_deps)):
    from ..sessions.service import submit_answer as _answer

    return _translate(_answer, deps, user_id=user_id, session_id=session_id, question_id=body.question_id,
                      response_text=body.response_text, selected_option_id=body.selected_option_id,
                      time_seconds=body.time_seconds, hints_used=body.hints_used)


@router.post("/{session_id}/end")
def end_session(session_id: str, user_id: str = Depends(get_user_id), deps=Depends(get_service_deps)):
    from ..sessions.service import end_session as _end

    return _translate(_end, deps, user_id=user_id, session_id=session_id)


@router.get("/{session_id}/summary")
def session_summary(session_id: str, user_id: str = Depends(get_user_id), deps=Depends(get_service_deps)):
    from ..sessions.service import get_session_summary as _summary

    return _translate(_summary, deps, user_id=user_id, session_id=session_id)
