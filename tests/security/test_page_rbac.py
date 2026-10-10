from __future__ import annotations

import base64
import json

import pytest

pytestmark = pytest.mark.security
READ_PAGES = [
    "/dashboard",
    "/vendors",
    "/documents",
    "/assessments",
    "/assessments/{assessment_a}",
]
PRIVATE_PAGES = READ_PAGES + ["/chat", "/profile", "/admin/system"]


@pytest.mark.parametrize("path", PRIVATE_PAGES)
def test_anonymous_direct_page_access_redirects_to_login(security_api, path):
    response = security_api.client.get(path.format(**security_api.ids))
    assert response.status_code == 302
    assert response.headers["location"] == "/sign-in"
    security_api.assert_no_work()


@pytest.mark.parametrize("path", READ_PAGES + ["/profile"])
@pytest.mark.parametrize("role", ["viewer", "analyst", "admin"])
def test_application_roles_can_open_read_pages(security_api, path, role):
    security_api.authenticate([role])
    response = security_api.client.get(path.format(**security_api.ids))
    assert response.status_code == 200


@pytest.mark.parametrize("path", READ_PAGES + ["/chat", "/admin/system"])
@pytest.mark.parametrize(
    "roles", [[], ["unknown-role"], "admin", None, ["admin", {}], ["ADMIN"]]
)
def test_roleless_and_malformed_sessions_cannot_read_private_pages(
    security_api, path, roles
):
    security_api.authenticate(user={"sub": "test-user", "roles": roles})
    response = security_api.client.get(path.format(**security_api.ids))
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "Your account needs access" in response.text
    assert 'action="/auth/login"' in response.text
    assert 'class="app-shell"' not in response.text
    assert "Private summary" not in response.text
    assert "Private finding" not in response.text
    security_api.assert_no_work()


@pytest.mark.parametrize(
    "role,path",
    [("viewer", "/chat"), ("viewer", "/admin/system"), ("analyst", "/admin/system")],
)
def test_direct_urls_enforce_privileged_roles(security_api, role, path):
    security_api.authenticate([role])
    assert security_api.client.get(path).status_code == 403
    security_api.assert_no_work()


@pytest.mark.parametrize("role", ["analyst", "admin"])
def test_writer_roles_can_open_chat(security_api, role):
    security_api.authenticate([role])
    assert security_api.client.get("/chat").status_code == 200


def test_admin_can_open_system_page(security_api):
    security_api.authenticate(["admin"])
    assert security_api.client.get("/admin/system").status_code == 200


def test_viewer_page_hides_write_controls_and_privileged_navigation(security_api):
    security_api.authenticate(["viewer"])
    response = security_api.client.get("/documents")
    assert 'id="document-upload-form"' not in response.text
    assert 'href="/chat"' not in response.text
    assert 'href="/admin/system"' not in response.text


def test_unassigned_user_profile_does_not_advertise_private_navigation(security_api):
    security_api.authenticate([])
    response = security_api.client.get("/profile")
    assert response.status_code == 200
    assert "Your account needs access" in response.text
    assert 'class="app-shell"' not in response.text
    for path in (
        "/dashboard",
        "/vendors",
        "/documents",
        "/assessments",
        "/chat",
        "/admin/system",
    ):
        assert f'href="{path}"' not in response.text
    security_api.assert_no_work()


def test_pages_issue_a_secure_session_cookie(security_api):
    security_api.authenticate(["viewer"], csrf_token=None)
    response = security_api.client.get("/profile")
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie
    assert "max-age=3600" in cookie


@pytest.mark.parametrize("token", [123, ["token"], {"token": "value"}, "é"])
def test_page_replaces_invalid_csrf_with_a_usable_token(security_api, token):
    security_api.authenticate(["analyst"], csrf_token=token)
    response = security_api.client.get("/profile")
    assert response.status_code == 200
    # Read the new signed cookie, then use that session's token in a real write.
    cookie = response.cookies["vendor_risk_session"]
    session = json.loads(base64.b64decode(security_api.signer.unsign(cookie)))
    replacement = session["csrf_token"]
    assert isinstance(replacement, str) and replacement.isascii() and replacement
    security_api.client.cookies.clear()
    security_api.client.cookies.set("vendor_risk_session", cookie)
    created = security_api.client.post(
        "/api/vendors",
        json={"name": "New Vendor"},
        headers={"X-CSRF-Token": replacement},
    )
    assert created.status_code == 201
