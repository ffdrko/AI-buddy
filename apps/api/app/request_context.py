"""Request-scoped logging context (Phase 7).

Middleware binds user/document/session ids from the JWT and path params so
every error log carries them. Read via ``get_context()`` anywhere.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("api")

_ctx: ContextVar[dict] = ContextVar("request_ctx", default={})


def get_context() -> dict:
    return dict(_ctx.get())


def log_error(msg: str, *args, **kwargs) -> None:
    ctx = _ctx.get()
    extra = " ".join(f"{k}={v}" for k, v in ctx.items() if v is not None)
    logger.error(f"{msg} {extra}".rstrip(), *args, **kwargs)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        ctx: dict = {}
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                from .auth import decode_token

                ctx["user_id"] = decode_token(auth.split(None, 1)[1], "access")
            except Exception:
                ctx["user_id"] = "invalid-token"
        for key in ("document_id", "session_id"):
            if key in request.path_params:
                ctx[key] = request.path_params[key]
        _ctx.set(ctx)
        try:
            return await call_next(request)
        finally:
            _ctx.set({})
