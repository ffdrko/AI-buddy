"""JWT auth with refresh tokens (BUILD_FLOW Phase 7).

Passwords: PBKDF2-HMAC-SHA256 via stdlib (no native deps). Tokens: HS256
JWTs (PyJWT). Access 30 min, refresh 7 days. SECRET_KEY must be set in prod;
a dev default is used only when APP_ENV != production (loud warning).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger("api.auth")

ACCESS_MINUTES = 30
REFRESH_DAYS = 7
_bearer = HTTPBearer(auto_error=False)


def _secret() -> str:
    secret = os.getenv("SECRET_KEY", "")
    if not secret:
        if os.getenv("APP_ENV") == "production":
            raise RuntimeError("SECRET_KEY must be set in production")
        logger.warning("using dev SECRET_KEY — set SECRET_KEY in production")
        return "dev-secret-change-me-use-a-long-random-value-in-prod"
    return secret


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"pbkdf2$200000${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, hexdk = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters))
        return hmac.compare_digest(dk.hex(), hexdk)
    except Exception:
        return False


def _encode(sub: str, kind: str, expires: datetime) -> str:
    import jwt

    return jwt.encode({"sub": sub, "type": kind, "exp": expires}, _secret(), algorithm="HS256")


def mint_tokens(user_id: str) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "access_token": _encode(user_id, "access", now + timedelta(minutes=ACCESS_MINUTES)),
        "refresh_token": _encode(user_id, "refresh", now + timedelta(days=REFRESH_DAYS)),
        "token_type": "bearer",
    }


def decode_token(token: str, expected_type: str) -> str:
    import jwt

    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid token")
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=401, detail="wrong token type")
    return str(payload["sub"])


def require_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """Route guard for all protected endpoints. Returns the user id."""
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="missing bearer token")
    return decode_token(creds.credentials, "access")
