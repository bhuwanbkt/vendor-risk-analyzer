"""Per-user rate limits for endpoints that spend Gemini credit.

Limits are shared by every application instance through PostgreSQL, so two
replicas do not double a user's allowance. Each user and limit has one row
in ``rate_limit_buckets``, updated under a row lock with GCRA (the generic
cell rate algorithm, a token bucket stored as a single timestamp):

- ``limit`` requests may be made in a burst;
- after that, one more becomes available every ``window / limit`` seconds.

So 20 per hour allows a burst of 20 and then one request every 3 minutes.
For a limit of one this is identical to a sliding window.

If the database cannot be reached, the request falls back to an in-memory
sliding window for that process instead of failing; the database outage is
logged. ``RATE_LIMIT_BACKEND=memory`` always uses the in-memory limiter.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
import time
from collections import deque
from datetime import datetime, timezone

from fastapi import HTTPException, Request, status

from vendor_risk_analyzer.auth.dependencies import get_current_user
from vendor_risk_analyzer.config import get_settings

logger = logging.getLogger("uvicorn.error")

# Share of allowed requests that also delete idle rows.
CLEANUP_PROBABILITY = 0.01


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"Rate limit exceeded; retry in {retry_after}s")
        self.retry_after = retry_after


# ------------------------------------------------------------
# In-memory fallback
# ------------------------------------------------------------


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


# ------------------------------------------------------------
# Shared (database) limiter
# ------------------------------------------------------------


def gcra(
    stored_tat: float | None,
    *,
    now: float,
    limit: int,
    window_seconds: float,
) -> tuple[float | None, int]:
    """Decide one request.

    Returns ``(new_tat, 0)`` when allowed, or ``(None, retry_after)``.
    """
    interval = window_seconds / limit
    tat = max(stored_tat if stored_tat is not None else now, now)
    new_tat = tat + interval
    if new_tat - now > window_seconds:
        return None, max(1, math.ceil(new_tat - window_seconds - now))
    return new_tat, 0


def default_session_factory():
    # Imported lazily so this module has no database side effects on import.
    from vendor_risk_analyzer.db.session import AsyncSessionLocal

    return AsyncSessionLocal()


# Replaceable in tests.
session_factory = default_session_factory


def bucket_key(raw: str) -> str:
    if len(raw) <= 255:
        return raw
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


async def hit_database(
    key: str,
    *,
    limit: int,
    window_seconds: float,
    now: float | None = None,
    factory=None,
) -> None:
    """Record one request in PostgreSQL, or raise RateLimitExceeded."""
    if limit <= 0:
        return
    from sqlalchemy import delete, select
    from sqlalchemy.exc import IntegrityError

    from vendor_risk_analyzer.db.models import RateLimitBucket

    key = bucket_key(key)
    now = time.time() if now is None else now
    for attempt in range(2):
        try:
            async with (factory or session_factory)() as db:
                # The row lock serializes simultaneous requests for one user.
                row = await db.scalar(
                    select(RateLimitBucket)
                    .where(RateLimitBucket.key == key)
                    .with_for_update()
                )
                new_tat, retry_after = gcra(
                    row.tat if row is not None else None,
                    now=now,
                    limit=limit,
                    window_seconds=window_seconds,
                )
                if new_tat is None:
                    await db.rollback()
                    raise RateLimitExceeded(retry_after)
                stamp = datetime.now(timezone.utc)
                if row is None:
                    db.add(RateLimitBucket(key=key, tat=new_tat, updated_at=stamp))
                else:
                    row.tat = new_tat
                    row.updated_at = stamp
                if random.random() < CLEANUP_PROBABILITY:
                    await db.execute(
                        delete(RateLimitBucket).where(RateLimitBucket.tat < now)
                    )
                await db.commit()
                return
        except IntegrityError:
            # Another instance created the row first; lock it and retry.
            if attempt == 1:
                raise


def bucket_limit(bucket: str) -> tuple[int, float]:
    settings = get_settings()
    if bucket == "assessment":
        return settings.assessment_rate_limit_per_hour, 3600.0
    if bucket == "chat":
        return settings.chat_rate_limit_per_minute, 60.0
    raise ValueError(f"Unknown rate-limit bucket: {bucket}")


async def enforce(bucket: str, *, organization_id: str | None, subject: str) -> None:
    limit, window = bucket_limit(bucket)
    key = f"{bucket}:{organization_id or '-'}:{subject}"
    if get_settings().rate_limit_backend == "database":
        try:
            await hit_database(key, limit=limit, window_seconds=window)
            return
        except RateLimitExceeded:
            raise
        except Exception:
            logger.exception(
                "Shared rate limit unavailable; using this process's limit. "
                "bucket=%s",
                bucket,
            )
    limiter.hit(key, limit=limit, window_seconds=window)


def rate_limit(bucket: str):
    """FastAPI dependency. Place after authentication and CSRF checks so
    rejected requests do not consume the caller's quota."""

    async def dependency(request: Request) -> None:
        user = get_current_user(request)
        try:
            await enforce(
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
