"""Cost tracking (BUILD_FLOW Phase 7).

Every AI Engine call is logged with user, operation, model, and tokens_used.
Aggregates are queryable per user/model via GET /usage/summary. The service
reports through a ``usage_sink`` callable so orchestration stays testable.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol


class UsageRecorder(Protocol):
    def __call__(self, entry: dict) -> None: ...


class MemoryUsageRecorder:
    def __init__(self):
        self.entries: list[dict] = []

    def __call__(self, entry: dict) -> None:
        self.entries.append(entry)


class SAUsageRecorder:
    def __init__(self, session_factory: Any):
        self.session_factory = session_factory

    def __call__(self, entry: dict) -> None:
        from .dbpath import ensure_db_path

        ensure_db_path()
        from db import models as m

        with self.session_factory() as s:
            s.add(m.AiUsageLog(user_id=entry["user_id"], session_id=entry.get("session_id"),
                               document_id=entry.get("document_id"), operation=entry["operation"],
                               model=entry["model"], tokens_used=entry.get("tokens_used", 0)))
            s.commit()


def summarize(entries: list[dict]) -> dict:
    by_model: dict[str, dict] = {}
    total = 0
    for e in entries:
        bucket = by_model.setdefault(e["model"], {"tokens": 0, "calls": 0})
        bucket["tokens"] += e.get("tokens_used", 0)
        bucket["calls"] += 1
        total += e.get("tokens_used", 0)
    return {"total_tokens": total, "total_calls": len(entries), "by_model": by_model}
