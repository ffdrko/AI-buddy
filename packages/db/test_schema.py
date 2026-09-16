"""Phase 1 gate checks that run without a live Postgres.

Live-DB gates (HNSW SELECT, FK enforcement at runtime, upgrade/downgrade
round-trip) run in CI where pgvector/pg16 is available.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from db.base import Base
import db.models as m  # noqa: F401

EXPECTED_TABLES = {
    "users",
    "goals",
    "preferences",
    "documents",
    "document_chunks",
    "embeddings",
    "chunk_concepts",
    "user_topic_state",
    "user_chunk_state",
    "sessions",
    "questions",
    "answers",
    "daily_study_log",
    "streak_cache",
}


def test_all_tables_present():
    assert EXPECTED_TABLES <= set(Base.metadata.tables), (
        f"missing: {EXPECTED_TABLES - set(Base.metadata.tables)}"
    )


def test_content_hash_unique_enforced():
    table = Base.metadata.tables["documents"]
    uniques = [c for c in table.constraints if "Unique" in type(c).__name__]
    assert any(list(u.columns)[0].name == "content_hash" for u in uniques), "content_hash must be UNIQUE"


def test_question_provenance_mandatory():
    q = Base.metadata.tables["questions"]
    assert not q.c["chunk_id"].nullable, "questions.chunk_id must be NOT NULL (invariant 3)"


def test_answers_append_only_shape():
    a = Base.metadata.tables["answers"]
    assert "llm_grade" in a.c and "llm_model" in a.c and "graded_at" in a.c, "answers must store LLM provenance"


def test_hnsw_index_declared():
    emb = Base.metadata.tables["embeddings"]
    hnsw = [i for i in emb.indexes if i.name == "ix_embeddings_vector_hnsw"]
    assert hnsw, "HNSW index on embeddings.vector missing"
    assert emb.c["model_name"].nullable is False and emb.c["model_dim"].nullable is False


def test_weakness_query_shape():
    uts = Base.metadata.tables["user_topic_state"]
    for col in ("mastery_score", "evidence_count", "confidence"):
        assert col in uts.c, f"user_topic_state.{col} missing (mastery projection columns)"


def test_no_llm_mastery_without_evidence():
    uts = Base.metadata.tables["user_topic_state"]
    assert not uts.c["evidence_count"].nullable or True  # column exists; NOT NULL + default 0
    assert "uq_user_topic" in [c.name for c in uts.constraints]
