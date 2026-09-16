"""Registration / login / refresh (Phase 7)."""

from __future__ import annotations

import os
import sys

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from ..dbpath import ensure_db_path

ensure_db_path()

from db.session import get_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from ..auth import decode_token, hash_password, mint_tokens, verify_password  # noqa: E402

router = APIRouter(prefix="/auth", tags=["auth"])
_refresh_bearer = HTTPBearer(auto_error=False)


class RegisterBody(BaseModel):
    email: str
    password: str
    display_name: str | None = None
    timezone: str = "UTC"


class LoginBody(BaseModel):
    email: str
    password: str


def _db():
    from db import models as m

    return m


@router.post("/register", status_code=201)
def register(body: RegisterBody):
    m = _db()
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    with factory() as s:
        if s.query(m.User).filter(m.User.email == body.email).first():
            raise HTTPException(status_code=409, detail="email already registered")
        user = m.User(email=body.email, display_name=body.display_name,
                      timezone=body.timezone, password_hash=hash_password(body.password))
        s.add(user)
        s.commit()
        s.refresh(user)
        tokens = mint_tokens(str(user.id))
        return {"user_id": str(user.id), **tokens}


@router.post("/login")
def login(body: LoginBody):
    m = _db()
    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    with factory() as s:
        user = s.query(m.User).filter(m.User.email == body.email).first()
        if user is None or not user.password_hash or not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="invalid credentials")
        tokens = mint_tokens(str(user.id))
        return {"user_id": str(user.id), **tokens}


@router.post("/refresh")
def refresh(creds: HTTPAuthorizationCredentials | None = Depends(_refresh_bearer)):
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="missing refresh token")
    user_id = decode_token(creds.credentials, "refresh")
    return {"user_id": user_id, **mint_tokens(user_id)}
