"""Per-session token cost (Phase 7). Queryable per user and model."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_user
from .sessions import _translate, get_session_store

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/summary")
def usage_summary(user_id: str = Depends(require_user), store=Depends(get_session_store)):
    from ..usage import summarize

    return _translate(summarize, store.get_usage(user_id))
