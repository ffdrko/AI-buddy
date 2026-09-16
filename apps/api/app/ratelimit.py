"""Per-user rate limiting for LLM-calling endpoints (BUILD_FLOW Phase 7).

In-memory sliding-window limiter (single process). Returns 429 when the
caller exceeds ``max_calls`` within ``window_s``. A Redis-backed limiter is
the multi-worker follow-up; the dep signature already isolates the swap.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException

from .auth import require_user

DEFAULT_MAX_CALLS = 30
DEFAULT_WINDOW_S = 60


@dataclass
class RateLimiter:
    max_calls: int = DEFAULT_MAX_CALLS
    window_s: int = DEFAULT_WINDOW_S
    _hits: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))

    def check(self, key: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        window = self._hits[key]
        while window and window[0] <= now - self.window_s:
            window.popleft()
        if len(window) >= self.max_calls:
            raise HTTPException(status_code=429, detail="rate limit exceeded; slow down")
        window.append(now)


_limiter = RateLimiter()


def rate_limited(user_id: str = Depends(require_user)) -> str:
    """Guard for LLM-calling endpoints: per user, per minute."""
    _limiter.check(f"llm:{user_id}")
    return user_id


def reset_limiter() -> None:
    _limiter._hits.clear()
