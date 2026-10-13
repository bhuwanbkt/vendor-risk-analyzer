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


# ------------------------------------------------------------
# GCRA (shared database limiter)
# ------------------------------------------------------------

from vendor_risk_analyzer.rate_limit import bucket_key, gcra  # noqa: E402


def run_gcra(times, *, limit, window):
    tat = None
    results = []
    for now in times:
        new_tat, retry_after = gcra(tat, now=now, limit=limit, window_seconds=window)
        if new_tat is not None:
            tat = new_tat
        results.append(retry_after)
    return results


def test_gcra_allows_a_burst_of_limit_then_refills_steadily():
    # 3 per minute: a burst of 3, then one every 20 seconds.
    results = run_gcra([0, 1, 2, 10, 20, 21, 40], limit=3, window=60)

    assert results == [0, 0, 0, 10, 0, 19, 0]


def test_gcra_with_limit_one_matches_a_sliding_window():
    results = run_gcra([0, 1, 59, 60], limit=1, window=60)

    assert results == [0, 59, 1, 0]


def test_gcra_rejections_do_not_extend_the_block():
    tat, _ = gcra(None, now=0, limit=1, window_seconds=60)
    for second in range(1, 60):
        new_tat, retry_after = gcra(tat, now=second, limit=1, window_seconds=60)
        assert new_tat is None
        assert retry_after == 60 - second

    assert gcra(tat, now=60, limit=1, window_seconds=60)[0] is not None


def test_long_keys_are_hashed_to_fit_the_column():
    assert bucket_key("chat:org:user") == "chat:org:user"
    hashed = bucket_key("x" * 400)
    assert hashed.startswith("sha256:") and len(hashed) <= 255
