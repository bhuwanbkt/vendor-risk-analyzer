"""Idle backoff for background workers.

A worker that polls the database every few seconds keeps a serverless
database (such as Neon) awake permanently, which uses compute hours even
when nobody is using the app. When a queue is empty, the wait between
checks doubles up to a maximum; any work found resets it. New work
queued by this process wakes the worker immediately through an event, so
backing off does not delay user actions.

The default maximum (600 seconds) is longer than Neon's five-minute
idle window, so the database can suspend between checks.
"""

from __future__ import annotations

import asyncio
import os


DEFAULT_IDLE_MAX_SECONDS = 600.0


def idle_max_seconds(name: str, *, minimum: float) -> float:
    """Read a worker's maximum idle wait; never below its base poll."""
    raw = os.getenv(name)
    if raw is None:
        return max(DEFAULT_IDLE_MAX_SECONDS, minimum)
    value = float(raw)
    if value < minimum:
        raise ValueError(
            f"{name} must be at least the worker poll interval ({minimum})."
        )
    return value


class IdleBackoff:
    """Doubling wait between empty polls, reset when work is found."""

    def __init__(self, base_seconds: float, max_seconds: float) -> None:
        if base_seconds <= 0:
            raise ValueError("base_seconds must be positive.")
        self.base_seconds = float(base_seconds)
        self.max_seconds = max(float(max_seconds), self.base_seconds)
        self.current = self.base_seconds

    def reset(self) -> None:
        self.current = self.base_seconds

    def next_delay(self) -> float:
        """Return the wait for this empty poll and grow the next one."""
        delay = self.current
        self.current = min(self.current * 2, self.max_seconds)
        return delay


async def wait_for_work(event: asyncio.Event, timeout: float) -> bool:
    """Wait until woken or until the timeout; True means woken."""
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except TimeoutError:
        return False
    return True
