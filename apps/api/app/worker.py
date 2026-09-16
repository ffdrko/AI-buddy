"""Background worker entrypoint (BUILD_FLOW Phase 2).

Production: jobs go through Redis/RQ (see docker-compose `worker` service).
Fallback: when Redis is unreachable, jobs run inline in the API process so
single-machine dev keeps working. The pipeline itself is idempotent-safe:
re-running marks failed rows and cleans partial artifacts first.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("api.worker")


def enqueue_ingestion(document_id: str, pdf_bytes: bytes, deps) -> str:
    """Enqueue (or inline-run) the ingestion job. Returns a job id or 'inline'."""
    from .pipeline import run_ingestion

    try:
        import redis  # type: ignore
        from rq import Queue  # type: ignore

        from .config import settings

        r = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        q: Queue = Queue("ingestion", connection=r)
        job = q.enqueue(run_ingestion, document_id, pdf_bytes, deps, job_timeout=1800)
        logger.info("enqueued ingestion doc=%s job=%s", document_id, job.id)
        return str(job.id)
    except Exception as e:
        logger.warning("queue unavailable (%s); running ingestion inline doc=%s", e, document_id)
        run_ingestion(document_id, pdf_bytes, deps)
        return "inline"
