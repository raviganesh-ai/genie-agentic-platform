"""Rate limiting for the one customer-triggerable write action on ``/cx``.

``app.api.cx``'s ``POST /cx/{session_id}/reanalyze`` is the only route that
lets a customer trigger real agent-routing work (everything else on the cx
surface is read-only). Since a cx access link can be shared/leaked beyond
its intended recipient, this module provides a simple, fail-closed,
in-memory fixed-window limiter keyed per ``session_id`` so no single
session can flood reanalysis requests. This is deliberately the same
"in-memory, single-process" tradeoff already accepted elsewhere in Phase
1-6 (e.g. ``memory_store_backend: in_memory``) - a durable, distributed
limiter (e.g. backed by Cosmos DB/Redis) is out of scope until this
service runs across multiple replicas.
"""
from __future__ import annotations

import threading
import time

from app.config.settings import Settings

__all__ = ["CxRateLimitExceededError", "CxRateLimiter", "create_cx_rate_limiter"]


class CxRateLimitExceededError(RuntimeError):
    """Raised when a session exceeds its allotted reanalysis request rate."""


class CxRateLimiter:
    """Fixed-window request counter, keyed per session id."""

    def __init__(self, *, limit: int, window_seconds: float = 3600.0) -> None:
        if limit <= 0:
            raise ValueError("limit must be a positive integer.")
        self._limit = limit
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        self._request_times: dict[str, list[float]] = {}

    def check(self, key: str) -> None:
        """Records one request for ``key``; raises if the window's limit is exceeded."""

        now = time.monotonic()
        with self._lock:
            window_start = now - self._window_seconds
            recent = [t for t in self._request_times.get(key, []) if t >= window_start]
            if len(recent) >= self._limit:
                raise CxRateLimitExceededError(
                    f"Reanalysis request rate limit exceeded for this session "
                    f"({self._limit} per {int(self._window_seconds)}s)."
                )
            recent.append(now)
            self._request_times[key] = recent


def create_cx_rate_limiter(settings: Settings) -> CxRateLimiter:
    return CxRateLimiter(limit=settings.cx_reanalysis_rate_limit_per_hour, window_seconds=3600.0)
