"""Session persistence (BUILD_FLOW Phase 5).

``SessionStore`` protocol keeps the orchestrator testable without a live DB;
``SASessionStore`` is the real implementation over Phase 1 models. All
methods return plain dicts. Learning projections (mastery, chunk state,
daily log, streak) are written here from the Learning Engine's pure outputs.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Protocol


def _models():
    from ..dbpath import ensure_db_path

    ensure_db_path()
    from db import models as m

    return m


class SessionStore(Protocol):
    # -- documents / users --
    def get_document(self, document_id: str) -> dict | None: ...
    def get_user_timezone(self, user_id: str) -> str | None: ...
    # -- sessions --
    def create_session(self, *, user_id: str, document_id: str, planned_minutes: int) -> dict: ...
    def get_session(self, session_id: str) -> dict | None: ...
    def set_session_ended(self, session_id: str, *, ended_at, actual_seconds: int) -> None: ...
    # -- chunks / concepts / embeddings --
    def list_chunks(self, document_id: str) -> list[dict]: ...
    def get_chunk_concepts(self, chunk_ids: list[str]) -> dict[str, list[tuple[str, float]]]: ...
    def get_embedding_models(self, document_id: str) -> list[str]: ...
    def vector_search(self, document_id: str, query_vector: list[float], top_k: int) -> list[tuple[str, float]]: ...
    # -- questions --
    def add_question(self, *, session_id: str, chunk_id: str, question_text: str, question_type: str,
                     options: list[dict] | None, correct_option_id: str | None,
                     rubric_chunk_ids: list[str], llm_model: str) -> dict: ...
    def next_unanswered(self, session_id: str) -> dict | None: ...
    def count_questions(self, session_id: str) -> int: ...
    def question_chunk_least_asked(self, session_id: str, document_id: str) -> dict | None: ...
    def get_question(self, question_id: str) -> dict | None: ...
    def get_chunks_by_ids(self, chunk_ids: list[str]) -> dict[str, dict]: ...
    # -- answers (append-only) --
    def add_answer(self, *, question_id: str, user_id: str, session_id: str, response_text: str | None,
                   selected_option_id: str | None, hints_used: int, time_seconds: int | None) -> dict: ...
    def set_answer_grade(self, answer_id: str, *, grade: float, feedback: str, model: str, graded_at) -> None: ...
    def list_answers(self, session_id: str) -> list[dict]: ...
    # -- learner projections --
    def get_topic_states(self, user_id: str) -> list[dict]: ...
    def upsert_topic_state(self, *, user_id: str, concept: str, mastery: float, evidence: int, confidence: float) -> None: ...
    def get_chunk_states(self, user_id: str, chunk_ids: list[str]) -> dict[str, dict]: ...
    def upsert_chunk_state(self, *, user_id: str, chunk_id: str, stability: float, difficulty: float,
                           next_due_at, last_reviewed_at, review_count: int) -> None: ...
    def upsert_daily_log(self, *, user_id: str, study_date, seconds: int, answered: int, correct: int) -> None: ...
    def get_streak(self, user_id: str) -> dict: ...
    def set_streak(self, *, user_id: str, current: int, longest: int, last_date) -> None: ...
    # -- read models for progress/dashboard --
    def list_sessions(self, user_id: str, limit: int = 10) -> list[dict]: ...
    def count_answers(self, session_id: str) -> int: ...
    def get_daily_logs(self, user_id: str, since) -> list[dict]: ...
    # -- cost tracking (Phase 7) --
    def log_usage(self, *, user_id: str, session_id: str | None, document_id: str | None,
                  operation: str, model: str, tokens_used: int) -> None: ...
    def get_usage(self, user_id: str) -> list[dict]: ...
    # -- background jobs (Phase 7) --
    def list_user_ids(self) -> list[str]: ...
    def get_all_study_dates(self, user_id: str) -> list: ...
    def embedding_model_counts(self) -> dict[str, int]: ...


class SASessionStore:
    def __init__(self, session_factory: Any):
        self.session_factory = session_factory

    # -- documents / users --
    def get_document(self, document_id: str) -> dict | None:
        m = _models()
        with self.session_factory() as s:
            d = s.query(m.Document).filter(m.Document.id == document_id).first()
            if d is None or getattr(d, "deleted_at", None):
                return None
            return {"id": str(d.id), "user_id": str(d.user_id), "status": d.status, "title": d.title}

    def get_user_timezone(self, user_id: str) -> str | None:
        m = _models()
        with self.session_factory() as s:
            u = s.query(m.User).filter(m.User.id == user_id).first()
            return u.timezone if u else None

    # -- sessions --
    def create_session(self, *, user_id: str, document_id: str, planned_minutes: int) -> dict:
        from datetime import datetime, timezone

        m = _models()
        with self.session_factory() as s:
            row = m.Session(user_id=user_id, document_id=document_id, status="active",
                            planned_duration_minutes=planned_minutes, started_at=datetime.now(timezone.utc))
            s.add(row)
            s.commit()
            s.refresh(row)
            return {"id": str(row.id), "status": row.status}

    def get_session(self, session_id: str) -> dict | None:
        m = _models()
        with self.session_factory() as s:
            r = s.query(m.Session).filter(m.Session.id == session_id).first()
            if r is None:
                return None
            return {"id": str(r.id), "user_id": str(r.user_id), "document_id": str(r.document_id),
                    "status": r.status, "planned_duration_minutes": r.planned_duration_minutes,
                    "started_at": r.started_at}

    def set_session_ended(self, session_id: str, *, ended_at, actual_seconds: int) -> None:
        m = _models()
        with self.session_factory() as s:
            r = s.query(m.Session).filter(m.Session.id == session_id).first()
            if r is None:
                return
            r.status = "completed"
            r.ended_at = ended_at
            r.actual_duration_seconds = actual_seconds
            s.commit()

    # -- chunks / concepts / embeddings --
    def list_chunks(self, document_id: str) -> list[dict]:
        m = _models()
        with self.session_factory() as s:
            rows = (s.query(m.DocumentChunk).filter(m.DocumentChunk.document_id == document_id)
                    .order_by(m.DocumentChunk.chunk_index).all())
            return [{"chunk_id": str(r.id), "chunk_index": r.chunk_index, "content": r.content,
                     "section_heading": r.section_heading, "page_start": r.page_start, "page_end": r.page_end}
                    for r in rows]

    def get_chunk_concepts(self, chunk_ids: list[str]) -> dict[str, list[tuple[str, float]]]:
        m = _models()
        out: dict[str, list[tuple[str, float]]] = {cid: [] for cid in chunk_ids}
        if not chunk_ids:
            return out
        with self.session_factory() as s:
            for r in s.query(m.ChunkConcept).filter(m.ChunkConcept.chunk_id.in_(chunk_ids)).all():
                out.setdefault(str(r.chunk_id), []).append((r.concept, r.confidence or 0.0))
        return out

    def get_embedding_models(self, document_id: str) -> list[str]:
        m = _models()
        with self.session_factory() as s:
            chunk_ids = [r[0] for r in s.query(m.DocumentChunk.id)
                         .filter(m.DocumentChunk.document_id == document_id).all()]
            if not chunk_ids:
                return []
            return [r[0] for r in s.query(m.Embedding.model_name)
                    .filter(m.Embedding.chunk_id.in_(chunk_ids)).distinct().all()]

    def vector_search(self, document_id: str, query_vector: list[float], top_k: int) -> list[tuple[str, float]]:
        m = _models()
        with self.session_factory() as s:
            rows = (s.query(m.Embedding.chunk_id, m.Embedding.vector.cosine_distance(query_vector).label("d"))
                    .join(m.DocumentChunk, m.DocumentChunk.id == m.Embedding.chunk_id)
                    .filter(m.DocumentChunk.document_id == document_id)
                    .order_by("d").limit(top_k).all())
            return [(str(cid), float(d)) for cid, d in rows]

    # -- questions --
    def add_question(self, *, session_id: str, chunk_id: str, question_text: str, question_type: str,
                     options: list[dict] | None, correct_option_id: str | None,
                     rubric_chunk_ids: list[str], llm_model: str) -> dict:
        m = _models()
        with self.session_factory() as s:
            row = m.Question(session_id=session_id, chunk_id=chunk_id, question_text=question_text,
                             question_type=question_type, options=options, correct_option_id=correct_option_id,
                             rubric_chunk_ids=rubric_chunk_ids, llm_model=llm_model)
            s.add(row)
            s.commit()
            s.refresh(row)
            return {"id": str(row.id)}

    def _question_dict(self, r) -> dict:
        return {"id": str(r.id), "session_id": str(r.session_id), "chunk_id": str(r.chunk_id),
                "question_text": r.question_text, "question_type": r.question_type,
                "options": r.options, "correct_option_id": r.correct_option_id,
                "rubric_chunk_ids": list(r.rubric_chunk_ids or []), "llm_model": r.llm_model,
                "generated_at": r.generated_at}

    def next_unanswered(self, session_id: str) -> dict | None:
        m = _models()
        with self.session_factory() as s:
            # IDs come back as UUID objects — normalise to str for comparison.
            answered = {str(r[0]) for r in s.query(m.Answer.question_id).filter(m.Answer.session_id == session_id).all()}
            for row in (s.query(m.Question).filter(m.Question.session_id == session_id)
                        .order_by(m.Question.generated_at).all()):
                if str(row.id) not in answered:
                    return self._question_dict(row)
            return None

    def count_questions(self, session_id: str) -> int:
        m = _models()
        with self.session_factory() as s:
            return s.query(m.Question).filter(m.Question.session_id == session_id).count()

    def question_chunk_least_asked(self, session_id: str, document_id: str) -> dict | None:
        """Chunk in this document with the fewest questions so far this session."""
        m = _models()
        with self.session_factory() as s:
            from sqlalchemy import func

            counts = dict(s.query(m.Question.chunk_id, func.count(m.Question.id))
                          .filter(m.Question.session_id == session_id).group_by(m.Question.chunk_id).all())
            chunks = (s.query(m.DocumentChunk).filter(m.DocumentChunk.document_id == document_id)
                      .order_by(m.DocumentChunk.chunk_index).all())
            if not chunks:
                return None
            best = min(chunks, key=lambda c: (counts.get(c.id, 0), c.chunk_index))
            return {"chunk_id": str(best.id), "content": best.content, "section_heading": best.section_heading}

    def get_question(self, question_id: str) -> dict | None:
        m = _models()
        with self.session_factory() as s:
            r = s.query(m.Question).filter(m.Question.id == question_id).first()
            return self._question_dict(r) if r else None

    def get_chunks_by_ids(self, chunk_ids: list[str]) -> dict[str, dict]:
        m = _models()
        with self.session_factory() as s:
            rows = s.query(m.DocumentChunk).filter(m.DocumentChunk.id.in_(chunk_ids)).all()
            return {str(r.id): {"chunk_id": str(r.id), "content": r.content,
                                "section_heading": r.section_heading, "page_start": r.page_start,
                                "page_end": r.page_end} for r in rows}

    # -- answers --
    def add_answer(self, *, question_id: str, user_id: str, session_id: str, response_text: str | None,
                   selected_option_id: str | None, hints_used: int, time_seconds: int | None) -> dict:
        m = _models()
        with self.session_factory() as s:
            row = m.Answer(question_id=question_id, user_id=user_id, session_id=session_id,
                           response_text=response_text, selected_option_id=selected_option_id,
                           hints_used=hints_used, time_seconds=time_seconds)
            s.add(row)
            s.commit()
            s.refresh(row)
            return {"id": str(row.id)}

    def set_answer_grade(self, answer_id: str, *, grade: float, feedback: str, model: str, graded_at) -> None:
        m = _models()
        with self.session_factory() as s:
            r = s.query(m.Answer).filter(m.Answer.id == answer_id).first()
            if r is None:
                return
            r.llm_grade = grade
            r.llm_feedback = feedback
            r.llm_model = model
            r.graded_at = graded_at
            s.commit()

    def list_answers(self, session_id: str) -> list[dict]:
        m = _models()
        with self.session_factory() as s:
            rows = (s.query(m.Answer).filter(m.Answer.session_id == session_id)
                    .order_by(m.Answer.created_at).all())
            return [{"id": str(r.id), "question_id": str(r.question_id), "response_text": r.response_text,
                     "selected_option_id": r.selected_option_id, "hints_used": r.hints_used,
                     "time_seconds": r.time_seconds, "llm_grade": r.llm_grade,
                     "llm_feedback": r.llm_feedback, "llm_model": r.llm_model} for r in rows]

    # -- learner projections --
    def get_topic_states(self, user_id: str) -> list[dict]:
        m = _models()
        with self.session_factory() as s:
            return [{"concept": r.concept, "mastery_score": r.mastery_score,
                     "evidence_count": r.evidence_count, "confidence": r.confidence}
                    for r in s.query(m.UserTopicState).filter(m.UserTopicState.user_id == user_id).all()]

    def upsert_topic_state(self, *, user_id: str, concept: str, mastery: float, evidence: int, confidence: float) -> None:
        from datetime import datetime, timezone

        m = _models()
        with self.session_factory() as s:
            r = (s.query(m.UserTopicState)
                 .filter(m.UserTopicState.user_id == user_id, m.UserTopicState.concept == concept).first())
            if r is None:
                r = m.UserTopicState(user_id=user_id, concept=concept)
                s.add(r)
            r.mastery_score = mastery
            r.evidence_count = evidence
            r.confidence = confidence
            r.last_updated = datetime.now(timezone.utc)
            s.commit()

    def get_chunk_states(self, user_id: str, chunk_ids: list[str]) -> dict[str, dict]:
        m = _models()
        with self.session_factory() as s:
            rows = (s.query(m.UserChunkState)
                    .filter(m.UserChunkState.user_id == user_id, m.UserChunkState.chunk_id.in_(chunk_ids)).all())
            return {str(r.chunk_id): {"stability": r.stability, "difficulty": r.difficulty,
                                      "next_due_at": r.next_due_at, "last_reviewed_at": r.last_reviewed_at,
                                      "review_count": r.review_count or 0} for r in rows}

    def upsert_chunk_state(self, *, user_id: str, chunk_id: str, stability: float, difficulty: float,
                           next_due_at, last_reviewed_at, review_count: int) -> None:
        m = _models()
        with self.session_factory() as s:
            r = (s.query(m.UserChunkState)
                 .filter(m.UserChunkState.user_id == user_id, m.UserChunkState.chunk_id == chunk_id).first())
            if r is None:
                r = m.UserChunkState(user_id=user_id, chunk_id=chunk_id)
                s.add(r)
            r.stability = stability
            r.difficulty = difficulty
            r.next_due_at = next_due_at
            r.last_reviewed_at = last_reviewed_at
            r.review_count = review_count
            s.commit()

    def upsert_daily_log(self, *, user_id: str, study_date, seconds: int, answered: int, correct: int) -> None:
        m = _models()
        with self.session_factory() as s:
            r = (s.query(m.DailyStudyLog)
                 .filter(m.DailyStudyLog.user_id == user_id, m.DailyStudyLog.study_date == study_date).first())
            if r is None:
                r = m.DailyStudyLog(user_id=user_id, study_date=study_date)
                s.add(r)
            r.total_seconds = (r.total_seconds or 0) + seconds
            r.questions_answered = (r.questions_answered or 0) + answered
            r.correct_count = (r.correct_count or 0) + correct
            s.commit()

    def get_streak(self, user_id: str) -> dict:
        m = _models()
        with self.session_factory() as s:
            r = s.query(m.StreakCache).filter(m.StreakCache.user_id == user_id).first()
            if r is None:
                return {"current_streak": 0, "longest_streak": 0, "last_study_date": None}
            return {"current_streak": r.current_streak or 0, "longest_streak": r.longest_streak or 0,
                    "last_study_date": r.last_study_date}

    def set_streak(self, *, user_id: str, current: int, longest: int, last_date) -> None:
        from datetime import datetime, timezone

        m = _models()
        with self.session_factory() as s:
            r = s.query(m.StreakCache).filter(m.StreakCache.user_id == user_id).first()
            if r is None:
                r = m.StreakCache(user_id=user_id)
                s.add(r)
            r.current_streak = current
            r.longest_streak = longest
            r.last_study_date = last_date
            r.updated_at = datetime.now(timezone.utc)
            s.commit()

    def list_sessions(self, user_id: str, limit: int = 10) -> list[dict]:
        m = _models()
        with self.session_factory() as s:
            rows = (s.query(m.Session).filter(m.Session.user_id == user_id)
                    .order_by(m.Session.started_at.desc()).limit(limit).all())
            return [{"id": str(r.id), "document_id": str(r.document_id), "status": r.status,
                     "planned_duration_minutes": r.planned_duration_minutes,
                     "started_at": r.started_at.isoformat() if r.started_at else None,
                     "ended_at": r.ended_at.isoformat() if r.ended_at else None} for r in rows]

    def count_answers(self, session_id: str) -> int:
        m = _models()
        with self.session_factory() as s:
            return s.query(m.Answer).filter(m.Answer.session_id == session_id).count()

    def get_daily_logs(self, user_id: str, since) -> list[dict]:
        m = _models()
        with self.session_factory() as s:
            rows = (s.query(m.DailyStudyLog)
                    .filter(m.DailyStudyLog.user_id == user_id, m.DailyStudyLog.study_date >= since)
                    .order_by(m.DailyStudyLog.study_date).all())
            return [{"study_date": r.study_date.isoformat(), "total_seconds": r.total_seconds or 0,
                     "questions_answered": r.questions_answered or 0, "correct_count": r.correct_count or 0}
                    for r in rows]

    def log_usage(self, *, user_id: str, session_id: str | None, document_id: str | None,
                  operation: str, model: str, tokens_used: int) -> None:
        m = _models()
        with self.session_factory() as s:
            s.add(m.AiUsageLog(user_id=user_id, session_id=session_id, document_id=document_id,
                               operation=operation, model=model, tokens_used=tokens_used))
            s.commit()

    def get_usage(self, user_id: str) -> list[dict]:
        m = _models()
        with self.session_factory() as s:
            rows = (s.query(m.AiUsageLog).filter(m.AiUsageLog.user_id == user_id)
                    .order_by(m.AiUsageLog.created_at).all())
            return [{"operation": r.operation, "model": r.model, "tokens_used": r.tokens_used or 0,
                     "session_id": str(r.session_id) if r.session_id else None} for r in rows]

    def list_user_ids(self) -> list[str]:
        m = _models()
        with self.session_factory() as s:
            return [str(r[0]) for r in s.query(m.User.id).all()]

    def get_all_study_dates(self, user_id: str) -> list:
        m = _models()
        with self.session_factory() as s:
            return [r[0] for r in s.query(m.DailyStudyLog.study_date)
                    .filter(m.DailyStudyLog.user_id == user_id).all()]

    def embedding_model_counts(self) -> dict[str, int]:
        m = _models()
        from sqlalchemy import func

        with self.session_factory() as s:
            return {model: count for model, count in
                    s.query(m.Embedding.model_name, func.count(m.Embedding.id)).group_by(m.Embedding.model_name).all()}
