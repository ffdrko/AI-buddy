"""Phase 1 — complete database schema.

Ground-truth rules (BUILD_FLOW.md):
- `answers` is the append-only event log; mastery/accuracy/streak are projections.
- `user_topic_state` is a cache recomputable from answers + chunk_concepts.
- Every question links to its source chunk (provenance mandatory).
"""

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))


def now_utc() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- Users ---
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)  # Phase 7; null for pre-auth rows
    timezone: Mapped[str] = mapped_column(Text, nullable=False)  # IANA string; streak correctness
    created_at: Mapped[datetime] = now_utc()


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    target_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = now_utc()


class Preference(Base):
    __tablename__ = "preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    daily_study_minutes: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    preferred_session_length_minutes: Mapped[int] = mapped_column(Integer, default=25, server_default="25")
    notification_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    updated_at: Mapped[datetime] = now_utc()


# -------------------------------------------------------------- Content ---
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text_key: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)  # sha256 of raw PDF bytes
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # soft delete (Phase 2)
    created_at: Mapped[datetime] = now_utc()

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','extracting','chunking','embedding','ready','failed')", name="ck_documents_status"
        ),
        Index("ix_documents_user_hash", "user_id", "content_hash"),
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    section_heading: Mapped[str | None] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = now_utc()

    __table_args__ = (Index("ix_chunks_doc_index", "document_id", "chunk_index"),)


class Embedding(Base):
    __tablename__ = "embeddings"

    id: Mapped[uuid.UUID] = uuid_pk()
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    model_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    embedded_at: Mapped[datetime] = now_utc()

    __table_args__ = (
        Index(
            "ix_embeddings_vector_hnsw",
            "vector",
            postgresql_using="hnsw",
            postgresql_ops={"vector": "vector_cosine_ops"},
        ),
    )


class ChunkConcept(Base):
    __tablename__ = "chunk_concepts"

    id: Mapped[uuid.UUID] = uuid_pk()
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    concept: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)


# --------------------------------------------------------- Learner model ---
class UserTopicState(Base):
    """Cache / projection. Ground truth recomputable from answers."""

    __tablename__ = "user_topic_state"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    concept: Mapped[str] = mapped_column(Text, nullable=False)
    mastery_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    last_updated: Mapped[datetime] = now_utc()

    __table_args__ = (UniqueConstraint("user_id", "concept", name="uq_user_topic"),)


class UserChunkState(Base):
    """FSRS scheduling state per chunk."""

    __tablename__ = "user_chunk_state"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False
    )
    stability: Mapped[float | None] = mapped_column(Float)
    difficulty: Mapped[float | None] = mapped_column(Float)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    __table_args__ = (UniqueConstraint("user_id", "chunk_id", name="uq_user_chunk"),)


# ------------------------------------------------------- Study sessions ---
class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="planned")
    planned_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    actual_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('planned','active','completed','abandoned')", name="ck_sessions_status"),
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="RESTRICT"), nullable=False
    )  # provenance — never null
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[dict | list | None] = mapped_column(JSONB)
    correct_option_id: Mapped[str | None] = mapped_column(Text)
    rubric_chunk_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    llm_model: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime] = now_utc()

    __table_args__ = (
        CheckConstraint("question_type IN ('mcq','free_text','true_false')", name="ck_questions_type"),
        CheckConstraint("chunk_id IS NOT NULL", name="ck_questions_chunk_not_null"),
    )


class Answer(Base):
    """Append-only event log. Never modified after write; corrections are new rows."""

    __tablename__ = "answers"

    id: Mapped[uuid.UUID] = uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    response_text: Mapped[str | None] = mapped_column(Text)
    selected_option_id: Mapped[str | None] = mapped_column(Text)
    hints_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    time_seconds: Mapped[int | None] = mapped_column(Integer)
    llm_grade: Mapped[float | None] = mapped_column(Float)
    llm_feedback: Mapped[str | None] = mapped_column(Text)
    llm_model: Mapped[str | None] = mapped_column(Text)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = now_utc()


# ------------------------------------------------- Progress (projections) ---
class DailyStudyLog(Base):
    __tablename__ = "daily_study_log"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    study_date: Mapped[date] = mapped_column(Date, nullable=False)  # date in user's timezone
    total_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    questions_answered: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    correct_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    __table_args__ = (UniqueConstraint("user_id", "study_date", name="uq_daily_log"),)


class StreakCache(Base):
    """Derived from daily_study_log. Always recomputable."""
    __tablename__ = "streak_cache"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    current_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    longest_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_study_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = now_utc()


class AiUsageLog(Base):
    """Cost tracking (Phase 7). Append-only; aggregates queried per user/model."""

    __tablename__ = "ai_usage_log"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    operation: Mapped[str] = mapped_column(Text, nullable=False)  # plan | generate | evaluate | tutor | embed
    model: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = now_utc()


__all__ = [
    "Base",
    "User",
    "Goal",
    "Preference",
    "Document",
    "DocumentChunk",
    "Embedding",
    "ChunkConcept",
    "UserTopicState",
    "UserChunkState",
    "Session",
    "Question",
    "Answer",
    "DailyStudyLog",
    "StreakCache",
    "AiUsageLog",
]
