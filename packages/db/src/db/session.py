import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://study:study@localhost:5432/study")

_engine = None
_SessionLocal = None


def get_engine():
    """Lazily create the engine so importing models never needs a live DB driver."""
    global _engine, _SessionLocal
    if _engine is None:
        # Pool config per Phase 0 spec (PgBouncer-ready; SQLAlchemy pool tuned in Phase 7)
        _engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_session():
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    with _SessionLocal() as session:
        yield session
