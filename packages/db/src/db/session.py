import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://study:study@localhost:5432/study")
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
POOL_MAX_OVERFLOW = int(os.getenv("DB_POOL_MAX_OVERFLOW", "20"))

_engine = None
_SessionLocal = None


def get_engine():
    """Lazily create the engine so importing models never needs a live DB driver."""
    global _engine, _SessionLocal
    if _engine is None:
        # PgBouncer-ready: prepared-statement-free usage, pre-ping, bounded pool.
        # In prod compose, route through the pgbouncer service via DATABASE_URL.
        _engine = create_engine(DATABASE_URL, pool_size=POOL_SIZE, max_overflow=POOL_MAX_OVERFLOW, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_session():
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    with _SessionLocal() as session:
        yield session
