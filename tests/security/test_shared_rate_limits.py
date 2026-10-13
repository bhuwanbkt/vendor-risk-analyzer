"""Rate limits are stored in the database and shared by all app instances."""

from __future__ import annotations

import pytest

from vendor_risk_analyzer import rate_limit

pytestmark = pytest.mark.security


def ask(api):
    return api.client.post(
        "/api/chat",
        json={"vendor_id": api.ids["vendor_a"], "question": "What evidence exists?"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )


def test_limit_is_shared_by_another_instance(security_api, monkeypatch):
    monkeypatch.setattr(rate_limit, "bucket_limit", lambda bucket: (1, 60.0))
    security_api.authenticate(["analyst"])

    assert ask(security_api).status_code == 200
    # A different process has its own memory but the same database.
    rate_limit.limiter.reset()
    limited = ask(security_api)

    assert limited.status_code == 429
    assert 0 < int(limited.headers["retry-after"]) <= 60
    assert security_api.cloud.chat_factory.call_count == 1


def test_database_outage_falls_back_to_the_process_limit(
    security_api, monkeypatch, caplog
):
    monkeypatch.setattr(rate_limit, "bucket_limit", lambda bucket: (1, 60.0))

    def unavailable():
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(rate_limit, "session_factory", unavailable)
    security_api.authenticate(["analyst"])

    with caplog.at_level("ERROR", logger="uvicorn.error"):
        assert ask(security_api).status_code == 200
        assert ask(security_api).status_code == 429

    assert "Shared rate limit unavailable" in caplog.text


def test_memory_backend_does_not_touch_the_database(security_api, monkeypatch):
    from vendor_risk_analyzer.config import get_settings

    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")
    get_settings.cache_clear()
    monkeypatch.setattr(rate_limit, "bucket_limit", lambda bucket: (1, 60.0))

    def must_not_be_used():
        raise AssertionError("memory backend used the database")

    monkeypatch.setattr(rate_limit, "session_factory", must_not_be_used)
    security_api.authenticate(["analyst"])

    assert ask(security_api).status_code == 200
    assert ask(security_api).status_code == 429
