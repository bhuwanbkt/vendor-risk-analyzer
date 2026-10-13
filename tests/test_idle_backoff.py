from __future__ import annotations

import asyncio

import pytest

from vendor_risk_analyzer.idle_backoff import (
    DEFAULT_IDLE_MAX_SECONDS,
    IdleBackoff,
    idle_max_seconds,
    wait_for_work,
)


pytestmark = pytest.mark.unit


def test_backoff_doubles_up_to_the_maximum_and_resets() -> None:
    backoff = IdleBackoff(5, 30)

    assert [backoff.next_delay() for _ in range(5)] == [5, 10, 20, 30, 30]

    backoff.reset()
    assert backoff.next_delay() == 5


def test_maximum_below_base_is_raised_to_base() -> None:
    backoff = IdleBackoff(10, 2)

    assert [backoff.next_delay() for _ in range(3)] == [10, 10, 10]


def test_default_idle_maximum_lets_a_serverless_database_suspend(
    monkeypatch,
) -> None:
    monkeypatch.delenv("TEST_IDLE_MAX", raising=False)

    # Neon suspends after five idle minutes.
    assert idle_max_seconds("TEST_IDLE_MAX", minimum=5) == DEFAULT_IDLE_MAX_SECONDS
    assert DEFAULT_IDLE_MAX_SECONDS > 300


def test_idle_maximum_can_disable_backoff(monkeypatch) -> None:
    monkeypatch.setenv("TEST_IDLE_MAX", "5")

    assert idle_max_seconds("TEST_IDLE_MAX", minimum=5) == 5


def test_idle_maximum_below_poll_interval_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("TEST_IDLE_MAX", "1")

    with pytest.raises(ValueError, match="at least the worker poll interval"):
        idle_max_seconds("TEST_IDLE_MAX", minimum=5)


def test_wait_for_work_returns_early_when_woken() -> None:
    async def run() -> tuple[bool, float]:
        event = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.call_later(0.01, event.set)
        started = loop.time()
        woken = await wait_for_work(event, timeout=30)
        return woken, loop.time() - started

    woken, elapsed = asyncio.run(run())

    assert woken is True
    assert elapsed < 5


def test_wait_for_work_times_out_when_idle() -> None:
    async def run() -> bool:
        return await wait_for_work(asyncio.Event(), timeout=0.01)

    assert asyncio.run(run()) is False
