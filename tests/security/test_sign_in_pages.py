from __future__ import annotations

import pytest

pytestmark = pytest.mark.security

PRIVATE_LINKS = (
    "/dashboard",
    "/vendors",
    "/documents",
    "/assessments",
    "/chat",
    "/admin/system",
)


def assert_public_screen(response):
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert 'action="/auth/login"' in response.text
    assert 'name="login_hint"' in response.text
    assert 'autocomplete="username"' in response.text
    assert 'type="password"' not in response.text
    assert 'class="app-shell"' not in response.text
    for path in PRIVATE_LINKS:
        assert f'href="{path}"' not in response.text
    for private_content in ("Vendor A", "Vendor B", "Private summary", "Private finding"):
        assert private_content not in response.text


def test_anonymous_root_opens_public_sign_in_without_starting_oidc(security_api):
    root = security_api.client.get("/")
    assert root.status_code == 302
    assert root.headers["location"] == "/sign-in"
    response = security_api.client.get(root.headers["location"])
    assert response.status_code == 200
    assert "Sign in to your workspace" in response.text
    assert_public_screen(response)
    assert "vendor_risk_session" not in response.cookies
    security_api.assert_no_work()


@pytest.mark.parametrize(
    "roles", [[], ["unknown-role"], "admin", None, ["admin", {}], ["ADMIN"]]
)
def test_roleless_root_and_sign_in_explain_access_without_core_ui(security_api, roles):
    security_api.authenticate(user={"sub": "test-user", "name": "Test User", "roles": roles})
    root = security_api.client.get("/")
    assert root.status_code == 302
    assert root.headers["location"] == "/sign-in"
    response = security_api.client.get(root.headers["location"])
    assert response.status_code == 200
    assert "Your account needs access" in response.text
    assert "Test User" in response.text
    assert "Ask your app administrator" in response.text
    assert "Sign in with another account" in response.text
    assert 'href="/auth/logout"' in response.text
    assert_public_screen(response)
    assert security_api.client.get("/api/vendors").status_code == 403
    assert security_api.client.get("/auth/me").status_code == 200
    security_api.assert_no_work()


@pytest.mark.parametrize("role", ["viewer", "analyst", "admin"])
@pytest.mark.parametrize("path", ["/", "/sign-in"])
def test_authorized_accounts_enter_the_dashboard(security_api, path, role):
    security_api.authenticate([role])
    response = security_api.client.get(path)
    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"
    security_api.assert_no_work()


def test_roleless_account_identity_is_escaped(security_api):
    security_api.authenticate(
        user={
            "sub": "test-user",
            "name": "<script>alert('name')</script>",
            "email": "<img src=x onerror=alert('email')>",
            "roles": [],
        }
    )
    response = security_api.client.get("/sign-in")
    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "<img src=x" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "&lt;img" in response.text
    security_api.assert_no_work()


def test_sign_out_is_visible_outside_the_profile_dropdown(security_api):
    security_api.authenticate(["analyst"])
    response = security_api.client.get("/profile")
    assert response.status_code == 200
    assert '<a href="/auth/logout" class="sign-out-button">Sign out</a>' in response.text
    security_api.assert_no_work()
