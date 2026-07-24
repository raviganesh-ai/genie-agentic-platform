"""Unit tests for ``app.security.cx_rate_limiter``."""
from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.security.cx_rate_limiter import (
    CxRateLimiter,
    CxRateLimitExceededError,
    create_cx_rate_limiter,
)


def test_allows_requests_up_to_the_limit() -> None:
    limiter = CxRateLimiter(limit=3, window_seconds=3600.0)

    limiter.check("session-1")
    limiter.check("session-1")
    limiter.check("session-1")


def test_raises_once_the_limit_is_exceeded() -> None:
    limiter = CxRateLimiter(limit=2, window_seconds=3600.0)

    limiter.check("session-1")
    limiter.check("session-1")
    with pytest.raises(CxRateLimitExceededError):
        limiter.check("session-1")


def test_limit_is_scoped_per_key() -> None:
    limiter = CxRateLimiter(limit=1, window_seconds=3600.0)

    limiter.check("session-1")
    limiter.check("session-2")
    with pytest.raises(CxRateLimitExceededError):
        limiter.check("session-1")


def test_old_requests_fall_outside_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = CxRateLimiter(limit=1, window_seconds=10.0)
    current_time = [1000.0]
    monkeypatch.setattr(
        "app.security.cx_rate_limiter.time.monotonic", lambda: current_time[0]
    )

    limiter.check("session-1")
    with pytest.raises(CxRateLimitExceededError):
        limiter.check("session-1")

    current_time[0] += 11.0
    limiter.check("session-1")


def test_construction_rejects_a_non_positive_limit() -> None:
    with pytest.raises(ValueError):
        CxRateLimiter(limit=0)


def test_create_cx_rate_limiter_reads_the_configured_limit() -> None:
    settings = Settings(cx_reanalysis_rate_limit_per_hour=2)
    limiter = create_cx_rate_limiter(settings)

    limiter.check("session-1")
    limiter.check("session-1")
    with pytest.raises(CxRateLimitExceededError):
        limiter.check("session-1")
