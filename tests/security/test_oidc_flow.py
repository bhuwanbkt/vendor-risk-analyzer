from __future__ import annotations

import base64
import hashlib
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey

pytestmark = pytest.mark.security


def session_data(api, response):
    cookie = response.cookies["vendor_risk_session"]
    return json.loads(base64.b64decode(api.signer.unsign(cookie, max_age=3600)))


@pytest.fixture(scope="session")
def oidc_signing_key():
    return RSAKey.generate_key(2048, parameters={"kid": "oidc-tests-only"})


@pytest.fixture
def oidc_provider(security_api, monkeypatch, oidc_signing_key):
    """Mock only provider HTTP responses; Authlib's OIDC verification stays real."""
    from vendor_risk_analyzer.auth import routes

    issuer = routes.settings.zitadel_issuer.rstrip("/")
    role_claim = (
        f"urn:zitadel:iam:org:project:{routes.settings.zitadel_project_id}:roles"
    )
    provider = SimpleNamespace(
        api=security_api,
        settings=routes.settings,
        issuer=issuer,
        role_claim=role_claim,
        requests=[],
        authorization={},
        subject="oidc-user",
        claims={},
        signing_key=oidc_signing_key,
        omit_tokens=set(),
        token_status=200,
        userinfo={
            "sub": "oidc-user",
            "name": "OIDC User",
            "email": "oidc-user@example.invalid",
            "preferred_username": "oidc-user",
            role_claim: {"analyst": {"test-org": "example.invalid"}},
        },
        userinfo_status=200,
        userinfo_text=None,
        userinfo_error=None,
    )

    def handle(request):
        assert str(request.url).startswith(issuer + "/")
        provider.requests.append(request)
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": issuer,
                    "authorization_endpoint": issuer + "/oauth/v2/authorize",
                    "token_endpoint": issuer + "/oauth/v2/token",
                    "jwks_uri": issuer + "/oauth/v2/keys",
                    "userinfo_endpoint": issuer + "/oidc/v1/userinfo",
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                },
            )
        if path == "/oauth/v2/keys":
            return httpx.Response(200, json={"keys": [oidc_signing_key.as_dict()]})
        if path == "/oauth/v2/token":
            if provider.token_status != 200:
                return httpx.Response(
                    provider.token_status, json={"error": "invalid_grant"}
                )
            now = int(time.time())
            claims = {
                "iss": issuer,
                "sub": provider.subject,
                "aud": routes.settings.zitadel_client_id,
                "iat": now,
                "exp": now + 300,
                "nonce": provider.authorization["nonce"],
                **provider.claims,
            }
            body = {
                "access_token": "test-access-token",
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": jwt.encode(
                    {"alg": "RS256", "kid": "oidc-tests-only"},
                    claims,
                    provider.signing_key,
                ),
            }
            for name in provider.omit_tokens:
                body.pop(name)
            return httpx.Response(200, json=body)
        if path == "/oidc/v1/userinfo":
            assert request.headers["authorization"] == "Bearer test-access-token"
            if provider.userinfo_error:
                raise provider.userinfo_error
            if provider.userinfo_text is not None:
                return httpx.Response(200, text=provider.userinfo_text)
            return httpx.Response(
                provider.userinfo_status,
                content=json.dumps(provider.userinfo),
                headers={"Content-Type": "application/json"},
            )
        raise AssertionError(f"Unexpected provider endpoint: {path}")

    original_init = httpx.AsyncClient.__init__

    def local_http(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", local_http)
    monkeypatch.setattr(routes.oauth.zitadel, "server_metadata", {})

    def begin(**params):
        response = security_api.client.get("/auth/login", params=params)
        assert response.status_code == 302
        provider.authorization = {
            name: values[0]
            for name, values in parse_qs(
                urlsplit(response.headers["location"]).query
            ).items()
        }
        return response

    def callback(**params):
        return security_api.client.get(
            "/auth/callback",
            params={
                "code": "test-code",
                "state": provider.authorization.get("state"),
                **params,
            },
        )

    def login():
        begin()
        response = callback()
        assert response.status_code == 303
        return response

    provider.begin = begin
    provider.callback = callback
    provider.login = login
    return provider


def test_login_uses_fixed_callback_state_nonce_and_s256_pkce(oidc_provider):
    provider = oidc_provider
    response = provider.begin(
        redirect_uri="https://attacker.example/callback", prompt="none"
    )
    params = provider.authorization
    assert response.headers["location"].startswith(
        provider.issuer + "/oauth/v2/authorize?"
    )
    assert params["client_id"] == provider.settings.zitadel_client_id
    assert params["redirect_uri"] == provider.settings.zitadel_redirect_uri
    assert set(params["scope"].split()) == {"openid", "profile", "email"}
    assert params["response_type"] == "code"
    assert params["prompt"] == "login"
    assert params["state"] and params["nonce"]
    assert params["code_challenge_method"] == "S256"
    assert "code_verifier" not in params
    saved = session_data(provider.api, response)
    state_data = saved[f"_state_zitadel_{params['state']}"]["data"]
    expected = (
        base64.urlsafe_b64encode(
            hashlib.sha256(state_data["code_verifier"].encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    assert params["code_challenge"] == expected
    assert provider.api.client.get("/auth/me").status_code == 401
    provider.api.assert_no_work()


def test_callback_verifies_identity_and_uses_original_pkce_verifier(oidc_provider):
    provider = oidc_provider
    login = provider.begin()
    state_data = session_data(provider.api, login)[
        f"_state_zitadel_{provider.authorization['state']}"
    ]["data"]
    response = provider.callback(code_verifier="attacker-verifier")
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    identity = provider.api.client.get("/auth/me").json()
    assert identity == {
        "sub": "oidc-user",
        "name": "OIDC User",
        "email": "oidc-user@example.invalid",
        "preferred_username": "oidc-user",
        "roles": ["analyst"],
    }
    token_request = next(
        r for r in provider.requests if r.url.path == "/oauth/v2/token"
    )
    body = parse_qs(token_request.content.decode())
    assert body["grant_type"] == ["authorization_code"]
    assert body["code"] == ["test-code"]
    assert body["code_verifier"] == [state_data["code_verifier"]]
    assert body["redirect_uri"] == [provider.settings.zitadel_redirect_uri]
    saved = session_data(provider.api, response)
    assert set(saved) == {"user", "csrf_token"}
    assert isinstance(saved["csrf_token"], str) and saved["csrf_token"]
    provider.api.assert_no_work()


@pytest.mark.parametrize("state", [None, "wrong-state"])
def test_callback_rejects_missing_or_wrong_state_before_token_exchange(
    oidc_provider, state
):
    provider = oidc_provider
    provider.begin()
    assert provider.callback(state=state).status_code == 401
    assert all(r.url.path != "/oauth/v2/token" for r in provider.requests)
    assert provider.api.client.get("/auth/me").status_code == 401
    provider.api.assert_no_work()


def test_callback_requires_the_browser_that_started_login(oidc_provider):
    provider = oidc_provider
    provider.begin()
    provider.api.client.cookies.clear()
    assert provider.callback().status_code == 401
    assert all(r.url.path != "/oauth/v2/token" for r in provider.requests)
    provider.api.assert_no_work()


def test_callback_state_is_consumed_in_the_updated_browser_session(oidc_provider):
    provider = oidc_provider
    provider.login()
    assert provider.callback().status_code == 401
    assert sum(r.url.path == "/oauth/v2/token" for r in provider.requests) == 1
    provider.api.assert_no_work()


@pytest.mark.parametrize(
    "claims",
    [
        {"nonce": "wrong-nonce"},
        {"iss": "https://attacker.example"},
        {"aud": "different-client"},
        {"exp": 1},
        {"sub": ""},
        {"sub": 123},
    ],
)
def test_invalid_id_token_claims_cannot_create_a_session(oidc_provider, claims):
    provider = oidc_provider
    provider.claims = claims
    provider.begin()
    assert provider.callback().status_code == 401
    assert all(r.url.path != "/oidc/v1/userinfo" for r in provider.requests)
    assert provider.api.client.get("/auth/me").status_code == 401
    provider.api.assert_no_work()


def test_invalid_id_token_signature_cannot_create_a_session(oidc_provider):
    provider = oidc_provider
    provider.signing_key = RSAKey.generate_key(2048)
    provider.begin()
    assert provider.callback().status_code == 401
    assert all(r.url.path != "/oidc/v1/userinfo" for r in provider.requests)
    assert provider.api.client.get("/auth/me").status_code == 401
    provider.api.assert_no_work()


@pytest.mark.parametrize("name", ["access_token", "id_token"])
def test_incomplete_token_response_cannot_create_a_session(oidc_provider, name):
    provider = oidc_provider
    provider.omit_tokens.add(name)
    provider.begin()
    assert provider.callback().status_code == 401
    assert all(r.url.path != "/oidc/v1/userinfo" for r in provider.requests)
    assert provider.api.client.get("/auth/me").status_code == 401
    provider.api.assert_no_work()


@pytest.mark.parametrize(
    "userinfo",
    [
        None,
        [],
        "user",
        123,
        {},
        {"sub": None},
        {"sub": ""},
        {"sub": 123},
        {"sub": "different-user"},
    ],
)
def test_invalid_or_mismatched_userinfo_cannot_create_a_session(
    oidc_provider, userinfo
):
    provider = oidc_provider
    provider.userinfo = userinfo
    provider.begin()
    assert provider.callback().status_code == 401
    assert provider.api.client.get("/auth/me").status_code == 401
    provider.api.assert_no_work()


@pytest.mark.parametrize("status", [401, 403, 500])
def test_userinfo_http_errors_cannot_create_a_session(oidc_provider, status):
    provider = oidc_provider
    provider.userinfo_status = status
    provider.begin()
    assert provider.callback().status_code == 401
    assert provider.api.client.get("/auth/me").status_code == 401
    provider.api.assert_no_work()


def test_malformed_userinfo_json_is_an_authentication_failure(oidc_provider):
    provider = oidc_provider
    provider.userinfo_text = "not-json"
    provider.begin()
    assert provider.callback().status_code == 401
    assert provider.api.client.get("/auth/me").status_code == 401
    provider.api.assert_no_work()


def test_userinfo_timeout_is_an_authentication_failure(oidc_provider):
    provider = oidc_provider
    provider.userinfo_error = httpx.ReadTimeout("provider timed out")
    provider.begin()
    assert provider.callback().status_code == 401
    assert provider.api.client.get("/auth/me").status_code == 401
    provider.api.assert_no_work()


@pytest.mark.parametrize("claim", [None, [], "admin", 123])
def test_malformed_project_roles_grant_no_application_access(oidc_provider, claim):
    provider = oidc_provider
    provider.userinfo[provider.role_claim] = claim
    provider.login()
    assert provider.api.client.get("/auth/me").json()["roles"] == []
    assert provider.api.client.get("/api/vendors").status_code == 403
    provider.api.assert_no_work()


def test_roles_from_other_projects_do_not_grant_application_access(oidc_provider):
    provider = oidc_provider
    provider.userinfo.pop(provider.role_claim)
    provider.userinfo["urn:zitadel:iam:org:project:other-project:roles"] = {"admin": {}}
    provider.userinfo["urn:zitadel:iam:org:project:roles"] = {"admin": {}}
    provider.login()
    assert provider.api.client.get("/auth/me").json()["roles"] == []
    assert provider.api.client.get("/api/vendors").status_code == 403
    provider.api.assert_no_work()


def test_new_login_drops_old_identity_and_rotates_csrf_after_account_change(
    oidc_provider,
):
    provider = oidc_provider
    provider.userinfo[provider.role_claim] = {"admin": {"test-org": "example.invalid"}}
    first_login = provider.login()
    old_csrf = session_data(provider.api, first_login)["csrf_token"]
    provider.subject = "different-user"
    provider.userinfo["sub"] = provider.subject
    provider.userinfo[provider.role_claim] = {
        "analyst": {"test-org": "example.invalid"}
    }
    provider.begin()
    assert provider.api.client.get("/auth/me").status_code == 401
    response = provider.callback()
    assert response.status_code == 303
    saved = session_data(provider.api, response)
    assert saved["user"]["sub"] == "different-user"
    assert saved["user"]["roles"] == ["analyst"]
    assert saved["csrf_token"] != old_csrf
    assert provider.api.client.get("/admin/system").status_code == 403
    rejected = provider.api.client.post(
        "/api/vendors", json={"name": "New Vendor"}, headers={"X-CSRF-Token": old_csrf}
    )
    assert rejected.status_code == 403
    provider.api.assert_no_work()
    accepted = provider.api.client.post(
        "/api/vendors",
        json={"name": "New Vendor"},
        headers={"X-CSRF-Token": saved["csrf_token"]},
    )
    assert accepted.status_code == 201


def test_failed_account_change_does_not_keep_previous_admin_identity(oidc_provider):
    provider = oidc_provider
    provider.userinfo[provider.role_claim] = {"admin": {"test-org": "example.invalid"}}
    provider.login()
    provider.claims = {"nonce": "wrong-nonce"}
    provider.begin()
    assert provider.callback().status_code == 401
    assert provider.api.client.get("/auth/me").status_code == 401
    assert provider.api.client.get("/api/vendors").status_code == 401
    provider.api.assert_no_work()


def test_token_endpoint_error_is_an_authentication_failure(oidc_provider):
    provider = oidc_provider
    provider.token_status = 400
    provider.begin()
    assert provider.callback().status_code == 401
    assert all(r.url.path != "/oidc/v1/userinfo" for r in provider.requests)
    assert provider.api.client.get("/auth/me").status_code == 401
    provider.api.assert_no_work()


def test_cancelled_login_does_not_exchange_a_code_or_create_a_session(oidc_provider):
    provider = oidc_provider
    provider.begin()
    response = provider.callback(error="access_denied", error_description="Cancelled")
    assert response.status_code == 401
    assert all(r.url.path != "/oauth/v2/token" for r in provider.requests)
    assert provider.api.client.get("/auth/me").status_code == 401
    provider.api.assert_no_work()


def test_logout_clears_app_session_and_identifies_client_to_zitadel(oidc_provider):
    provider = oidc_provider
    provider.login()
    before = len(provider.requests)
    response = provider.api.client.get(
        "/auth/logout",
        params={
            "post_logout_redirect_uri": "https://attacker.example",
            "logout_hint": "another-user",
        },
    )
    assert response.status_code == 303
    target = urlsplit(response.headers["location"])
    assert target.scheme + "://" + target.netloc == provider.issuer
    assert target.path == "/oidc/v1/end_session"
    assert parse_qs(target.query) == {
        "client_id": [provider.settings.zitadel_client_id],
        "post_logout_redirect_uri": [provider.settings.zitadel_post_logout_uri],
        "logout_hint": ["oidc-user"],
    }
    assert "vendor_risk_session" not in provider.api.client.cookies
    assert provider.api.client.get("/auth/me").status_code == 401
    assert provider.api.client.get("/api/vendors").status_code == 401
    assert provider.api.client.get("/dashboard").headers["location"] == "/sign-in"
    # Simulate the provider returning to the configured app root after logout.
    returned = provider.api.client.get("/")
    assert returned.headers["location"] == "/sign-in"
    landing = provider.api.client.get(returned.headers["location"])
    assert landing.status_code == 200
    assert "Sign in to your workspace" in landing.text
    assert 'class="app-shell"' not in landing.text
    assert len(provider.requests) == before
    provider.api.assert_no_work()


@pytest.mark.parametrize("login_name", ["viewer", "analyst+test@example.invalid"])
def test_login_uses_the_entered_account_and_preserves_url_encoding(
    oidc_provider, login_name
):
    provider = oidc_provider
    response = provider.begin(login_hint=f"  {login_name}  ", prompt="select_account")
    assert provider.authorization["login_hint"] == login_name
    assert provider.authorization["prompt"] == "login"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    provider.api.assert_no_work()


def test_empty_login_hint_is_not_forwarded(oidc_provider):
    provider = oidc_provider
    provider.begin(login_hint="  ")
    assert "login_hint" not in provider.authorization
    assert provider.authorization["prompt"] == "login"


def test_login_rejects_an_oversized_hint_before_provider_requests(oidc_provider):
    provider = oidc_provider
    response = provider.api.client.get("/auth/login", params={"login_hint": "a" * 321})
    assert response.status_code == 422
    assert provider.requests == []
    provider.api.assert_no_work()


@pytest.mark.parametrize("login_name", [None, 123, [], {}, "", "   ", "a" * 321])
def test_logout_ignores_malformed_provider_login_names(oidc_provider, login_name):
    provider = oidc_provider
    provider.userinfo["preferred_username"] = login_name
    provider.login()
    response = provider.api.client.get("/auth/logout")
    assert response.status_code == 303
    assert "logout_hint" not in parse_qs(urlsplit(response.headers["location"]).query)
    assert provider.api.client.get("/auth/me").status_code == 401
    provider.api.assert_no_work()


def test_login_hint_never_sets_identity_or_grants_the_requested_role(oidc_provider):
    provider = oidc_provider
    provider.begin(login_hint="admin")
    assert provider.callback().status_code == 303
    identity = provider.api.client.get("/auth/me").json()
    assert identity["sub"] == provider.subject
    assert identity["roles"] == ["analyst"]
    assert provider.api.client.get("/admin/system").status_code == 403
    provider.api.assert_no_work()


def test_oidc_login_without_a_project_role_opens_the_access_screen(oidc_provider):
    provider = oidc_provider
    provider.userinfo.pop(provider.role_claim)
    completed = provider.login()
    root = provider.api.client.get(completed.headers["location"])
    assert root.headers["location"] == "/sign-in"
    page = provider.api.client.get(root.headers["location"])
    assert page.status_code == 200
    assert "Your account needs access" in page.text
    assert "OIDC User" in page.text
    assert 'action="/auth/login"' in page.text
    assert 'class="app-shell"' not in page.text
    assert 'href="/vendors"' not in page.text
    assert provider.api.client.get("/api/vendors").status_code == 403
    provider.api.assert_no_work()


def test_logout_encodes_the_authenticated_account_name(oidc_provider):
    provider = oidc_provider
    provider.userinfo["preferred_username"] = "analyst+test@example.invalid"
    provider.login()
    response = provider.api.client.get("/auth/logout")
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["logout_hint"] == ["analyst+test@example.invalid"]
    assert provider.api.client.get("/auth/me").status_code == 401
    provider.api.assert_no_work()


def test_logout_encodes_the_entire_configured_return_uri(oidc_provider, monkeypatch):
    provider = oidc_provider
    return_uri = "https://testserver/?next=/dashboard&source=logout#done"
    monkeypatch.setattr(provider.settings, "zitadel_post_logout_uri", return_uri)
    response = provider.api.client.get("/auth/logout")
    assert response.status_code == 303
    target = urlsplit(response.headers["location"])
    assert parse_qs(target.query) == {
        "client_id": [provider.settings.zitadel_client_id],
        "post_logout_redirect_uri": [return_uri],
    }
    assert target.fragment == ""
    assert provider.requests == []
    provider.api.assert_no_work()
