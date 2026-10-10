from __future__ import annotations

import base64
import json
import time
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.security

READ_CASES = [
    ("GET", "/api/vendors", None, 200),
    ("GET", "/api/vendors/{vendor_a}/documents", None, 200),
    ("GET", "/api/assessments/{assessment_a}", None, 200),
]
WRITE_CASES = [
    ("POST", "/api/vendors", {"name": "New Vendor"}, 201),
    (
        "POST",
        "/api/vendors/{vendor_a}/documents/upload-url",
        {
            "filename": "new-policy.txt",
            "content_type": "text/plain",
            "size_bytes": 5,
            "sha256": "c" * 64,
        },
        200,
    ),
    ("POST", "/api/vendors/{vendor_a}/documents/{document_a}/complete", None, 200),
    ("POST", "/api/vendors/{vendor_a}/documents/{document_a}/ingest", None, 200),
    ("POST", "/api/vendors/{vendor_a}/assessments", None, 201),
    (
        "POST",
        "/api/chat",
        {"vendor_id": "{vendor_a}", "question": "What evidence is available?"},
        200,
    ),
]


def send(api, case, *, headers=None):
    method, path, payload, _ = case
    if payload is not None:
        payload = {
            key: value.format(**api.ids) if isinstance(value, str) else value
            for key, value in payload.items()
        }
    return api.client.request(
        method, path.format(**api.ids), json=payload, headers=headers
    )


@pytest.mark.parametrize("case", READ_CASES + WRITE_CASES)
def test_anonymous_api_requests_are_rejected_before_work(security_api, case):
    response = send(security_api, case)
    assert response.status_code == 401
    security_api.assert_no_work()


@pytest.mark.parametrize("case", READ_CASES + WRITE_CASES)
@pytest.mark.parametrize("roles", [[], ["unknown-role"]])
def test_unassigned_roles_cannot_access_api(security_api, case, roles):
    security_api.authenticate(roles)
    response = send(security_api, case, headers={"X-CSRF-Token": "test-csrf-token"})
    assert response.status_code == 403
    security_api.assert_no_work()


@pytest.mark.parametrize("case", READ_CASES)
@pytest.mark.parametrize("role", ["viewer", "analyst", "admin"])
def test_application_roles_can_read_api(security_api, case, role):
    security_api.authenticate([role])
    assert send(security_api, case).status_code == 200


@pytest.mark.parametrize("case", WRITE_CASES)
def test_viewer_cannot_write_even_with_valid_csrf(security_api, case):
    security_api.authenticate(["viewer"])
    response = send(security_api, case, headers={"X-CSRF-Token": "test-csrf-token"})
    assert response.status_code == 403
    security_api.assert_no_work()


@pytest.mark.parametrize("case", WRITE_CASES)
@pytest.mark.parametrize("role", ["analyst", "admin"])
def test_writer_roles_can_call_api_with_valid_csrf(security_api, case, role):
    security_api.authenticate([role])
    response = send(security_api, case, headers={"X-CSRF-Token": "test-csrf-token"})
    assert response.status_code == case[3], response.text


@pytest.mark.parametrize("case", WRITE_CASES)
@pytest.mark.parametrize("headers", [None, {"X-CSRF-Token": "wrong-token"}])
def test_missing_or_wrong_csrf_is_rejected_before_work(security_api, case, headers):
    security_api.authenticate(["analyst"])
    response = send(security_api, case, headers=headers)
    assert response.status_code == 403
    security_api.assert_no_work()


@pytest.mark.parametrize("case", WRITE_CASES)
def test_missing_session_csrf_is_rejected_before_work(security_api, case):
    security_api.authenticate(["admin"], csrf_token=None)
    response = send(security_api, case, headers={"X-CSRF-Token": "test-csrf-token"})
    assert response.status_code == 403
    security_api.assert_no_work()


def test_non_ascii_csrf_header_is_rejected_without_server_error(security_api):
    security_api.authenticate(["analyst"])
    response = send(security_api, WRITE_CASES[0], headers=[(b"X-CSRF-Token", b"\xe9")])
    assert response.status_code == 403
    security_api.assert_no_work()


@pytest.mark.parametrize("token", [123, ["token"], {"token": "value"}])
def test_malformed_session_csrf_fails_closed(security_api, token):
    security_api.authenticate(["analyst"], csrf_token=token)
    response = send(
        security_api, WRITE_CASES[0], headers={"X-CSRF-Token": "test-csrf-token"}
    )
    assert response.status_code == 403
    security_api.assert_no_work()


@pytest.mark.parametrize(
    "roles", [None, "admin", 1, {"admin": True}, ["admin", {}], ["ADMIN"]]
)
def test_malformed_role_claims_fail_closed(security_api, roles):
    security_api.authenticate(user={"sub": "test-user", "roles": roles})
    response = security_api.client.get("/api/vendors")
    assert response.status_code == 403
    security_api.assert_no_work()


@pytest.mark.parametrize("user", ["test-user", ["admin"], 123, {}])
def test_malformed_session_identity_is_not_authenticated(security_api, user):
    security_api.authenticate(user=user)
    assert security_api.client.get("/api/vendors").status_code == 401
    assert security_api.client.get("/auth/me").status_code == 401
    for path in ("/", "/profile"):
        response = security_api.client.get(path)
        assert response.status_code == 302
        assert response.headers["location"] == "/auth/login"
    security_api.assert_no_work()


@pytest.mark.parametrize("roles", [[], ["viewer"], ["analyst"], ["admin"]])
def test_authenticated_user_can_read_own_identity(security_api, roles):
    security_api.authenticate(roles)
    response = security_api.client.get("/auth/me")
    assert response.status_code == 200
    assert response.json()["sub"] == "test-user"
    assert response.json()["roles"] == roles
    security_api.assert_no_work()


def test_identity_endpoint_requires_authentication(security_api):
    assert security_api.client.get("/auth/me").status_code == 401
    security_api.assert_no_work()


def test_editing_viewer_cookie_cannot_grant_admin_access(security_api):
    original = security_api.authenticate(["viewer"])
    _, signature = original.split(".", 1)
    forged_data = {
        "user": {"sub": "test-user", "roles": ["admin"]},
        "csrf_token": "test-csrf-token",
    }
    payload = base64.b64encode(json.dumps(forged_data).encode()).decode()
    security_api.client.cookies.set("vendor_risk_session", payload + "." + signature)

    assert security_api.client.get("/api/vendors").status_code == 401
    assert security_api.client.get("/admin/system").status_code == 302
    security_api.assert_no_work()


def test_expired_session_cannot_access_api(security_api, monkeypatch):
    monkeypatch.setattr(
        security_api.signer, "get_timestamp", Mock(return_value=int(time.time()) - 7200)
    )
    security_api.authenticate(["admin"])
    assert security_api.client.get("/api/vendors").status_code == 401
    security_api.assert_no_work()


def test_bearer_and_role_headers_do_not_replace_session_auth(security_api):
    response = security_api.client.get(
        "/api/vendors",
        headers={
            "Authorization": "Bearer untrusted-token",
            "X-Role": "admin",
        },
    )
    assert response.status_code == 401
    security_api.assert_no_work()
