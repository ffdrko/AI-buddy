"""LLM provider abstraction (BUILD_FLOW Phase 3).

- All calls go through ``call_with_retry``: bounded retries + hard timeout, so
  no request blocks indefinitely.
- Provider resolution: ``AI_ENGINE_PROVIDER=stub`` forces the deterministic
  offline stub; ``openai`` requires ``OPENAI_API_KEY``. Default is stub when
  no key is set, openai otherwise.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import time
from typing import Protocol

logger = logging.getLogger("api.ai")

DEFAULT_TIMEOUT_S = 30
DEFAULT_MAX_RETRIES = 3
CHAT_MODEL_DEFAULT = os.getenv("CHAT_MODEL", "gpt-4o-mini")


class ChatProvider(Protocol):
    name: str

    def chat(self, *, system: str, user: str, model: str, timeout_s: int, max_tokens: int) -> tuple[str, int]:
        """Return (response_text, tokens_used)."""
        ...


def call_with_retry(fn, *, max_retries: int = DEFAULT_MAX_RETRIES, timeout_s: int = DEFAULT_TIMEOUT_S, label: str = "llm"):
    """Run ``fn`` with a hard timeout per attempt and exponential backoff."""
    delay = 1.0
    last: Exception | None = None
    for attempt in range(1, max_retries + 1):
        # NOTE: never use `with ThreadPoolExecutor` here — __exit__ would
        # shutdown(wait=True) and block on the very thread we timed out.
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(fn)
            return future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            last = TimeoutError(f"{label} timed out after {timeout_s}s (attempt {attempt}/{max_retries})")
            logger.warning("%s timeout attempt=%d", label, attempt)
        except Exception as e:  # noqa: BLE001 - retry on any provider error
            last = e
            logger.warning("%s error attempt=%d err=%s", label, attempt, e)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if attempt < max_retries:
            time.sleep(delay)
            delay *= 2.0
    raise RuntimeError(f"{label} failed after {max_retries} attempts: {last}") from last


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def message_text(msg) -> str:
    """Reasoning-model aware: content first, reasoning fallback, else empty."""
    return (getattr(msg, "content", None) or getattr(msg, "reasoning", None) or "").strip()


class StubProvider:
    """Deterministic offline provider. Marks output so it is never billed as real."""

    name = "stub"

    def chat(self, *, system: str, user: str, model: str, timeout_s: int, max_tokens: int) -> tuple[str, int]:
        text = f"[stub:{model}] {user[:500]}"
        return text, estimate_tokens(system) + estimate_tokens(user) + estimate_tokens(text)


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("openai package is required for the OpenAI provider") from e
        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        kwargs: dict = {}
        base_url = os.getenv("OPENAI_BASE_URL")  # e.g. https://openrouter.ai/api/v1
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(api_key=key, **kwargs)

    def chat(self, *, system: str, user: str, model: str, timeout_s: int, max_tokens: int) -> tuple[str, int]:
        client = self._client

        def _call():
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=max_tokens,
                timeout=timeout_s,
            )
            choice = message_text(resp.choices[0].message)
            used = 0
            try:
                used = int(resp.usage.total_tokens or 0) if resp.usage else 0
            except (TypeError, ValueError):
                used = 0
            return choice, used or (estimate_tokens(system) + estimate_tokens(user) + estimate_tokens(choice))

        return call_with_retry(_call, timeout_s=timeout_s, label="openai.chat")


def get_provider(explicit: ChatProvider | str | None = None) -> ChatProvider:
    if isinstance(explicit, str):
        if explicit == "stub":
            return StubProvider()
        if explicit == "openai":
            return OpenAIProvider()
        raise ValueError(f"unknown AI provider: {explicit!r}")
    if explicit is not None:
        return explicit
    forced = os.getenv("AI_ENGINE_PROVIDER", "").lower()
    if forced == "stub":
        return StubProvider()
    if forced == "openai":
        return OpenAIProvider()
    if os.getenv("OPENAI_API_KEY"):
        return OpenAIProvider()
    return StubProvider()
