"""Sliding-window rate limiter used for Gemini-backed endpoints."""

from __future__ import annotations

import pytest

from vendor_risk_analyzer.rate_limit import (
    RateLimitExceeded,
    SlidingWindowLimiter,
)


pytestmark = pytest.mark.unit


def test_limit_blocks_until_the_window_slides():
    limiter = SlidingWindowLimiter()

    for second in range(3):
        limiter.hit("user", limit=3, window_seconds=60, now=float(second))

    with pytest.raises(RateLimitExceeded) as exc:
        limiter.hit("user", limit=3, window_seconds=60, now=10.0)
    assert exc.value.retry_after == 50

    # The first hit (t=0) expires at t=60.
    limiter.hit("user", limit=3, window_seconds=60, now=60.0)


def test_users_are_counted_separately_and_zero_disables():
    limiter = SlidingWindowLimiter()

    limiter.hit("alice", limit=1, window_seconds=60, now=0.0)
    limiter.hit("bob", limit=1, window_seconds=60, now=0.0)
    with pytest.raises(RateLimitExceeded):
        limiter.hit("alice", limit=1, window_seconds=60, now=1.0)

    for _ in range(100):
        limiter.hit("carol", limit=0, window_seconds=60, now=0.0)


def test_rejected_requests_do_not_extend_the_block():
    limiter = SlidingWindowLimiter()
    limiter.hit("user", limit=1, window_seconds=60, now=0.0)

    for second in range(1, 50):
        with pytest.raises(RateLimitExceeded):
            limiter.hit("user", limit=1, window_seconds=60, now=float(second))

    limiter.hit("user", limit=1, window_seconds=60, now=60.0)
