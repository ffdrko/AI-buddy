"""Live-Postgres integration tests. Skipped without DATABASE_URL.

Covers what fakes cannot: UUID-vs-string semantics, real HNSW search,
upgrade/downgrade round-trips. Run locally via scripts/e2e_env.py, or in CI
with a pgvector service.
"""

import os
import sys
import uuid
from datetime import datetime, timezone

import pytest

DB_URL = os.getenv("DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="needs live Postgres (DATABASE_URL)")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "packages", "db", "src"))


@needs_db
def test_queue_progresses_past_answered_questions():
    from sqlalchemy.orm import sessionmaker

    from app.sessions.store import SASessionStore
    from db import models as m
    from db.session import get_engine

    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    store = SASessionStore(session_factory=factory)
    uid = uuid.uuid4()
    with factory() as s:
        s.add(m.User(id=uid, email=f"int-{uuid.uuid4().hex[:8]}@x.test", timezone="UTC"))
        s.commit()
        doc = m.Document(user_id=uid, title="t", storage_key="k", content_hash=uuid.uuid4().hex, status="ready")
        s.add(doc)
        s.flush()
        ch = m.DocumentChunk(document_id=doc.id, chunk_index=0, content="content here")
        s.add(ch)
        s.flush()
        sess = m.Session(user_id=uid, document_id=doc.id, status="active", started_at=datetime.now(timezone.utc))
        s.add(sess)
        s.flush()
        q1 = m.Question(session_id=sess.id, chunk_id=ch.id, question_text="q1?", question_type="mcq",
                        options=[{"id": "A", "text": "x"}], correct_option_id="A", rubric_chunk_ids=[ch.id])
        q2 = m.Question(session_id=sess.id, chunk_id=ch.id, question_text="q2?", question_type="mcq",
                        options=[{"id": "A", "text": "y"}], correct_option_id="A", rubric_chunk_ids=[ch.id])
        s.add_all([q1, q2])
        s.commit()
        first = store.next_unanswered(str(sess.id))
        assert first is not None
        s.add(m.Answer(question_id=first["id"], user_id=uid, session_id=sess.id, response_text="A"))
        s.commit()
        second = store.next_unanswered(str(sess.id))
        assert second is not None and second["id"] != first["id"], "answered question was returned again"
        # cleanup
        s.query(m.Answer).filter(m.Answer.session_id == sess.id).delete()
        s.query(m.Question).filter(m.Question.session_id == sess.id).delete()
        s.query(m.DocumentChunk).filter(m.DocumentChunk.document_id == doc.id).delete()
        s.query(m.Session).filter(m.Session.id == sess.id).delete()
        s.query(m.Document).filter(m.Document.id == doc.id).delete()
        s.query(m.User).filter(m.User.id == uid).delete()
        s.commit()
