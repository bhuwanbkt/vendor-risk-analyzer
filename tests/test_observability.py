from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from vendor_risk_analyzer import observability


pytestmark = pytest.mark.unit


def test_disabled_without_dsn(monkeypatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    assert observability.init_error_tracking() is False


def test_scrub_event_removes_secrets_and_request_content() -> None:
    event = {
        "request": {
            "url": "https://app.example/api/chat",
            "data": {"question": "confidential vendor detail"},
            "cookies": {"vendor_risk_session": "secret"},
            "env": {"REMOTE_ADDR": "203.0.113.9"},
            "headers": {
                "Authorization": "Bearer token",
                "Cookie": "vendor_risk_session=secret",
                "X-CSRF-Token": "csrf",
                "User-Agent": "browser",
            },
        },
        "user": {"ip_address": "203.0.113.9", "id": "u1"},
    }

    scrubbed = observability.scrub_event(event)

    request = scrubbed["request"]
    assert "data" not in request
    assert "cookies" not in request
    assert "env" not in request
    assert request["headers"] == {"User-Agent": "browser"}
    assert scrubbed["user"] == {"id": "u1"}


def test_breadcrumb_urls_lose_their_query_strings() -> None:
    crumb = {
        "category": "httplib",
        "data": {
            "url": "https://storage.example/bucket/file.pdf?X-Amz-Signature=secret",
            "http.query": "X-Amz-Signature=secret",
            "method": "GET",
        },
    }
    event = {"breadcrumbs": {"values": [dict(crumb, data=dict(crumb["data"]))]}}

    assert observability.scrub_breadcrumb(crumb)["data"] == {
        "url": "https://storage.example/bucket/file.pdf",
        "method": "GET",
    }
    scrubbed = observability.scrub_event(event)["breadcrumbs"]["values"][0]
    assert "secret" not in str(scrubbed)


def test_invalid_trace_sample_rate_is_rejected() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        observability._sample_rate("2")


SCRIPT = textwrap.dedent(
    """
    import json, logging, sys
    import sentry_sdk

    events = []
    real_init = sentry_sdk.init

    def capture_init(**kwargs):
        options.update(kwargs)
        kwargs["transport"] = events.append
        return real_init(**kwargs)

    options = {}
    sentry_sdk.init = capture_init

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from vendor_risk_analyzer.observability import init_error_tracking

    assert init_error_tracking() is True
    app = FastAPI()

    @app.post("/boom")
    async def boom():
        raise RuntimeError("route failed")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/boom",
        json={"question": "confidential vendor detail"},
        headers={"Authorization": "Bearer secret-token", "X-CSRF-Token": "csrf-value-123"},
        cookies={"vendor_risk_session": "session-secret"},
    )
    assert response.status_code == 500

    try:
        raise ValueError("worker failed")
    except ValueError:
        logging.getLogger("uvicorn.error").exception("Background job failed")

    sentry_sdk.flush()
    print(json.dumps({
        "options": {
            "send_default_pii": options["send_default_pii"],
            "include_local_variables": options["include_local_variables"],
            "max_request_body_size": options["max_request_body_size"],
            "traces_sample_rate": options["traces_sample_rate"],
            "disabled": [i.__name__ for i in options["disabled_integrations"]],
        },
        "events": events,
    }, default=str))
    """
)


def test_real_sdk_reports_errors_without_sensitive_data(tmp_path) -> None:
    pytest.importorskip("sentry_sdk")
    pytest.importorskip("fastapi.testclient")

    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
        "SENTRY_DSN": "https://public@example.invalid/1",
        "SENTRY_ENVIRONMENT": "test",
    }
    env.pop("SENTRY_TRACES_SAMPLE_RATE", None)
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["options"] == {
        "send_default_pii": False,
        "include_local_variables": False,
        "max_request_body_size": "never",
        "traces_sample_rate": 0.0,
        "disabled": ["GoogleGenAIIntegration"],
    }

    events = payload["events"]
    messages = json.dumps(events)
    assert "route failed" in messages
    assert "worker failed" in messages
    # Nothing confidential leaves the process.
    for secret in (
        "confidential vendor detail",
        "secret-token",
        "session-secret",
        "csrf-value-123",
    ):
        assert secret not in messages
    for event in events:
        assert event["environment"] == "test"
        for frame_holder in event.get("exception", {}).get("values", []):
            for frame in frame_holder.get("stacktrace", {}).get("frames", []):
                assert "vars" not in frame
