"""Security headers on every response and per-user Gemini rate limits."""

from __future__ import annotations

import pytest

from vendor_risk_analyzer import rate_limit

pytestmark = pytest.mark.security


def test_pages_and_errors_carry_security_headers(security_api):
    for response in (
        security_api.client.get("/sign-in"),
        security_api.client.get("/api/vendors"),  # 401 without a session
        security_api.client.get("/health"),
    ):
        headers = response.headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        # Pages may set a stricter policy (sign-in uses no-referrer);
        # the middleware never overrides a page's own header.
        assert headers["referrer-policy"] in {
            "no-referrer",
            "strict-origin-when-cross-origin",
        }
        assert "max-age=" in headers["strict-transport-security"]

        csp = headers["content-security-policy"]
        assert "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "'unsafe-inline'" not in csp
        # Browser uploads go straight to object storage.
        assert "https://storage.example.invalid" in csp
        # The sign-in form redirects to the identity provider.
        assert "form-action 'self' https://identity.example.invalid" in csp


def test_chat_is_rate_limited_per_user(security_api, monkeypatch):
    monkeypatch.setattr(rate_limit, "bucket_limit", lambda bucket: (2, 60.0))
    security_api.authenticate(["analyst"])
    vendor = security_api.ids["vendor_a"]

    def ask():
        return security_api.client.post(
            "/api/chat",
            json={"vendor_id": vendor, "question": "What evidence is available?"},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )

    assert ask().status_code == 200
    assert ask().status_code == 200
    limited = ask()
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0
    assert security_api.cloud.chat_factory.call_count == 2


def test_requests_rejected_by_csrf_do_not_use_quota(security_api, monkeypatch):
    monkeypatch.setattr(rate_limit, "bucket_limit", lambda bucket: (1, 60.0))
    security_api.authenticate(["analyst"])
    vendor = security_api.ids["vendor_a"]
    url = f"/api/vendors/{vendor}/assessments"

    for _ in range(3):
        assert security_api.client.post(url).status_code == 403

    allowed = security_api.client.post(url, headers={"X-CSRF-Token": "test-csrf-token"})
    assert allowed.status_code == 202


def test_default_referrer_policy_applies_when_page_sets_none(security_api):
    response = security_api.client.get("/health")

    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_app_database_engine_hides_sql_parameters(security_api):
    from vendor_risk_analyzer.db.session import engine

    # SQL errors then show "[SQL parameters hidden]" instead of row values,
    # so logs and error tracking never contain document text or questions.
    assert engine.sync_engine.hide_parameters is True
