"""Progress/dashboard read model (Phase 6). Derived, never stored."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..routers.documents import get_user_id
from .sessions import _translate, get_service_deps

router = APIRouter(prefix="/progress", tags=["progress"])


@router.get("/summary")
def progress_summary(user_id: str = Depends(get_user_id), deps=Depends(get_service_deps)):
    from ..sessions.service import get_progress_summary as _progress

    return _translate(_progress, deps, user_id=user_id)
