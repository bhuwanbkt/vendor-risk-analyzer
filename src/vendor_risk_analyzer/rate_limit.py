"""Per-user rate limits for endpoints that spend Gemini credit.

Limits are kept in process memory with a sliding window, which suits a
single application instance. With several replicas each instance keeps its
own counts, so the effective limit is multiplied by the replica count.
"""

from __future__ import annotations

import math
import time
from collections import deque

from fastapi import HTTPException, Request, status

from vendor_risk_analyzer.auth.dependencies import get_current_user
from vendor_risk_analyzer.config import get_settings


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"Rate limit exceeded; retry in {retry_after}s")
        self.retry_after = retry_after


class SlidingWindowLimiter:
    MAX_KEYS = 10_000

    def __init__(self):
        self._hits: dict[str, deque[float]] = {}

    def hit(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: float,
        now: float | None = None,
    ) -> None:
        """Record one request, or raise RateLimitExceeded. limit <= 0 disables."""
        if limit <= 0:
            return
        now = time.monotonic() if now is None else now
        cutoff = now - window_seconds
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, math.ceil(hits[0] + window_seconds - now))
            raise RateLimitExceeded(retry_after)
        hits.append(now)
        if len(self._hits) > self.MAX_KEYS:
            self._prune(cutoff)

    def _prune(self, cutoff: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()


limiter = SlidingWindowLimiter()


def bucket_limit(bucket: str) -> tuple[int, float]:
    settings = get_settings()
    if bucket == "assessment":
        return settings.assessment_rate_limit_per_hour, 3600.0
    if bucket == "chat":
        return settings.chat_rate_limit_per_minute, 60.0
    raise ValueError(f"Unknown rate-limit bucket: {bucket}")


def enforce(bucket: str, *, organization_id: str | None, subject: str) -> None:
    limit, window = bucket_limit(bucket)
    limiter.hit(
        f"{bucket}:{organization_id or '-'}:{subject}",
        limit=limit,
        window_seconds=window,
    )


def rate_limit(bucket: str):
    """FastAPI dependency. Place after authentication and CSRF checks so
    rejected requests do not consume the caller's quota."""

    def dependency(request: Request) -> None:
        user = get_current_user(request)
        try:
            enforce(
                bucket,
                organization_id=user.get("organization_id"),
                subject=user["sub"],
            )
        except RateLimitExceeded as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again shortly.",
                headers={"Retry-After": str(exc.retry_after)},
            ) from exc

    return dependency
