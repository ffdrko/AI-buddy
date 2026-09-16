"""Typed I/O contracts for the AI Engine (BUILD_FLOW Phase 3).

The AI Engine accepts structured context assembled by the backend — it never
reads the DB directly. Every response carries ``model`` and ``tokens_used``
for cost tracking. LLM output is an observation, never a state transition.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

QuestionType = Literal["mcq", "free_text", "true_false"]
SessionGoal = Literal["reinforce", "explore", "weak_focus", "mixed"]
ChatRole = Literal["user", "assistant", "system"]


class RetrievedChunk(BaseModel):
    chunk_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    section_heading: str | None = None
    page_start: int | None = None
    page_end: int | None = None


class ChatTurn(BaseModel):
    role: ChatRole
    content: str


# ---------------------------------------------------------------- Tutor ---
class TutorRequest(BaseModel):
    user_query: str = Field(min_length=1)
    retrieved_chunks: list[RetrievedChunk] = Field(min_length=1)
    conversation_history: list[ChatTurn] = Field(default_factory=list)


class TutorResponse(BaseModel):
    response_text: str
    source_chunk_ids: list[str] = Field(min_length=1)  # grounding is mandatory
    model: str
    tokens_used: int = Field(ge=0)


# ----------------------------------------------------- Question generator --
class MCQOption(BaseModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class GenerateQuestionRequest(BaseModel):
    chunk_id: str = Field(min_length=1)
    chunk_content: str = Field(min_length=1)
    section_heading: str | None = None
    question_type: QuestionType
    concepts: list[str] = Field(default_factory=list)
    avoid_question_ids: list[str] = Field(default_factory=list)


class GeneratedQuestion(BaseModel):
    question_text: str = Field(min_length=1)
    question_type: QuestionType
    options: list[MCQOption] | None = None
    correct_option_id: str | None = None
    rubric_chunk_ids: list[str] = Field(min_length=1)  # provenance mandatory
    model: str
    tokens_used: int = Field(ge=0)


# -------------------------------------------------------------- Evaluator --
class EvaluateRequest(BaseModel):
    question_text: str = Field(min_length=1)
    user_response: str = Field(min_length=1)
    selected_option_id: str | None = None
    rubric_chunks: list[RetrievedChunk] = Field(min_length=1)
    question_type: QuestionType
    correct_option_id: str | None = None


class Evaluation(BaseModel):
    grade: float = Field(ge=0.0, le=1.0)
    is_correct: bool  # grade >= threshold; threshold is a backend decision
    feedback: str
    model: str
    tokens_used: int = Field(ge=0)


# ---------------------------------------------------------------- Planner --
class TopicState(BaseModel):
    concept: str
    mastery_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class ChunkState(BaseModel):
    chunk_id: str
    next_due_at: str | None = None  # ISO timestamp; None = never reviewed
    review_count: int = Field(ge=0, default=0)


class PlanRequest(BaseModel):
    user_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    user_topic_state: list[TopicState] = Field(default_factory=list)
    user_chunk_state: list[ChunkState] = Field(default_factory=list)
    chunk_ids: list[str] = Field(min_length=1, description="Candidate chunks in this document")
    chunk_concepts: dict[str, list[str]] = Field(
        default_factory=dict, description="chunk_id -> concept labels (from chunk_concepts)"
    )
    available_minutes: int = Field(gt=0)
    session_goal: SessionGoal = "mixed"


class StudyPlan(BaseModel):
    recommended_chunk_ids: list[str]
    estimated_duration_minutes: int = Field(ge=0)
    rationale: str
    model: str
    tokens_used: int = Field(ge=0)
