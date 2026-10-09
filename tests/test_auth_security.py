from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from vendor_risk_analyzer.auth.dependencies import (
    get_current_user,
    require_roles,
    verify_csrf,
)


pytestmark = pytest.mark.security


def make_request(
    *,
    session: dict | None = None,
    headers: dict[str, str] | None = None,
) -> Request:
    raw_headers = [
        (
            key.lower().encode("latin-1"),
            value.encode("latin-1"),
        )
        for key, value in (
            headers or {}
        ).items()
    ]

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
        "session": session or {},
    }

    return Request(scope)


def test_get_current_user_requires_authentication() -> None:
    request = make_request()

    with pytest.raises(
        HTTPException
    ) as exc_info:
        get_current_user(request)

    assert (
        exc_info.value.status_code
        == 401
    )


def test_require_roles_rejects_unauthorized_role() -> None:
    request = make_request(
        session={
            "user": {
                "roles": [
                    "viewer"
                ]
            }
        }
    )

    dependency = require_roles(
        "analyst",
        "admin",
    )

    with pytest.raises(
        HTTPException
    ) as exc_info:
        dependency(request)

    assert (
        exc_info.value.status_code
        == 403
    )


def test_require_roles_allows_authorized_role() -> None:
    user = {
        "roles": [
            "analyst"
        ]
    }

    request = make_request(
        session={
            "user": user
        }
    )

    dependency = require_roles(
        "analyst",
        "admin",
    )

    assert (
        dependency(request)
        == user
    )


def test_verify_csrf_rejects_missing_header() -> None:
    request = make_request(
        session={
            "csrf_token":
                "expected-token"
        }
    )

    with pytest.raises(
        HTTPException
    ) as exc_info:
        verify_csrf(request)

    assert (
        exc_info.value.status_code
        == 403
    )


def test_verify_csrf_rejects_mismatch() -> None:
    request = make_request(
        session={
            "csrf_token":
                "expected-token"
        },
        headers={
            "X-CSRF-Token":
                "wrong-token"
        },
    )

    with pytest.raises(
        HTTPException
    ) as exc_info:
        verify_csrf(request)

    assert (
        exc_info.value.status_code
        == 403
    )


def test_verify_csrf_accepts_matching_token() -> None:
    request = make_request(
        session={
            "csrf_token":
                "expected-token"
        },
        headers={
            "X-CSRF-Token":
                "expected-token"
        },
    )

    verify_csrf(request)
