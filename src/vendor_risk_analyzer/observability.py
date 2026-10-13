"""Optional error tracking with Sentry.

Enabled only when SENTRY_DSN is set. Unhandled request errors are reported
by Sentry's FastAPI integration; worker failures are reported through the
logging integration, because the workers log every failure with
``logger.exception`` (or ``logger.critical`` when a worker stops).

Vendor documents and chat questions are confidential, so the setup keeps
everything that could contain them out of Sentry:

- no request bodies, cookies, auth or CSRF headers, or client IPs
- no local variables in stack traces (they can hold document text)
- the Google GenAI integration is disabled, so prompts and model answers
  are never attached
- database engines hide bound parameters, so SQL errors in events and logs
  never contain row values (see ``db/session.py``)
- breadcrumbs keep no URL query strings (presigned storage URLs carry
  signatures there)
- performance tracing is off by default (free plans have small quotas)

Requires ``sentry-sdk>=2.42`` (the first version with the GenAI integration).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from vendor_risk_analyzer.version import APP_VERSION


logger = logging.getLogger("uvicorn.error")

# Header names compared in lower case.
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-csrf-token",
        "proxy-authorization",
    }
)


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None):
    """Remove request data that could contain secrets or vendor content."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("cookies", "data", "env"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                name: value
                for name, value in headers.items()
                if name.lower() not in SENSITIVE_HEADERS
            }
    user = event.get("user")
    if isinstance(user, dict):
        user.pop("ip_address", None)
    breadcrumbs = event.get("breadcrumbs")
    values = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else breadcrumbs
    for crumb in values or []:
        scrub_breadcrumb(crumb)
    return event


def scrub_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any] | None = None):
    """Drop URL query strings, which can carry signatures or identifiers."""
    data = crumb.get("data") if isinstance(crumb, dict) else None
    if isinstance(data, dict):
        for key in ("http.query", "http.fragment"):
            data.pop(key, None)
        url = data.get("url")
        if isinstance(url, str) and "?" in url:
            data["url"] = url.split("?", 1)[0]
    return crumb


def _sample_rate(raw: str | None) -> float:
    if raw is None or not raw.strip():
        return 0.0
    value = float(raw)
    if not 0.0 <= value <= 1.0:
        raise ValueError("SENTRY_TRACES_SAMPLE_RATE must be between 0 and 1.")
    return value


def init_error_tracking() -> bool:
    """Start Sentry if configured. Returns True when it was enabled."""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False

    try:
        import sentry_sdk
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed; "
            "error tracking is disabled."
        )
        return False

    disabled = []
    try:
        from sentry_sdk.integrations.google_genai import GoogleGenAIIntegration

        disabled.append(GoogleGenAIIntegration)
    except Exception:
        # Older SDKs have no GenAI integration, so it cannot attach prompts.
        pass

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT")
        or os.getenv("APP_ENV", "development"),
        release=f"vendor-risk-analyzer@{APP_VERSION}",
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=_sample_rate(os.getenv("SENTRY_TRACES_SAMPLE_RATE")),
        disabled_integrations=disabled,
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
    )
    logger.info("Sentry error tracking enabled.")
    return True
