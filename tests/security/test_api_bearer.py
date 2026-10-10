from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey

pytestmark = pytest.mark.security


@pytest.fixture(scope="module")
def signing_keys():
    return [
        RSAKey.generate_key(2048, parameters={"kid": name})
        for name in ("api-key-one", "api-key-two")
    ]


@pytest.fixture
def bearer_api(security_api, monkeypatch, signing_keys):
    from vendor_risk_analyzer.auth import bearer
    from vendor_risk_analyzer.config import get_settings

    monkeypatch.setenv("API_BEARER_ENABLED", "true")
    get_settings.cache_clear()
    settings = get_settings()
    bearer.get_bearer_verifier.cache_clear()
    provider = SimpleNamespace(
        api=security_api,
        issuer=settings.zitadel_issuer,
        project=settings.zitadel_project_id,
        keys=[signing_keys[0]],
        requests=[],
        status=200,
        document=None,
        clock_offset=0,
    )
    clock = time.monotonic
    monkeypatch.setattr(
        bearer,
        "time",
        SimpleNamespace(monotonic=lambda: clock() + provider.clock_offset),
    )

    def handle(request):
        assert str(request.url) == provider.issuer + "/oauth/v2/keys"
        provider.requests.append(request)
        document = (
            provider.document
            if provider.document is not None
            else {"keys": [key.as_dict() for key in provider.keys]}
        )
        return httpx.Response(provider.status, json=document)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        bearer.httpx,
        "AsyncClient",
        lambda *args, **kwargs: original(
            *args, **kwargs, transport=httpx.MockTransport(handle)
        ),
    )

    def token(roles=("analyst",), *, updates=None, omit=(), key=None, header=None):
        now = int(time.time())
        claims = {
            "iss": provider.issuer,
            "aud": [provider.project],
            "sub": "api-user",
            "jti": "access-token-test",
            "iat": now,
            "exp": now + 300,
            "nbf": now - 1,
            f"urn:zitadel:iam:org:project:{provider.project}:roles": {
                role: {"test-org": "example.invalid"} for role in roles
            },
        }
        claims.update(updates or {})
        for name in omit:
            claims.pop(name, None)
        key = key or signing_keys[0]
        return jwt.encode(header or {"alg": "RS256", "kid": key.kid}, claims, key)

    provider.token = token
    provider.headers = lambda **kwargs: {"Authorization": "Bearer " + token(**kwargs)}
    yield provider
    bearer.get_bearer_verifier.cache_clear()


@pytest.mark.parametrize("role", ["viewer", "analyst", "admin"])
def test_access_token_reads_api_without_creating_browser_session(bearer_api, role):
    api = bearer_api.api
    headers = bearer_api.headers(roles=[role])
    response = api.client.get("/api/vendors", headers=headers)
    assert response.status_code == 200
    assert {row["id"] for row in response.json()} == {
        api.ids["vendor_a"],
        api.ids["vendor_b"],
    }
    assert "set-cookie" not in response.headers
    me = api.client.get("/auth/me", headers=headers)
    assert me.json() == {"sub": "api-user", "roles": [role]}
    assert len(bearer_api.requests) == 1


@pytest.mark.parametrize(
    "role,status", [("viewer", 403), ("analyst", 201), ("admin", 201)]
)
def test_write_role_is_enforced_without_cookie_csrf(bearer_api, role, status):
    api = bearer_api.api
    response = api.client.post(
        "/api/vendors",
        headers=bearer_api.headers(roles=[role]),
        json={"name": "API vendor"},
    )
    assert response.status_code == status
    if status == 403:
        api.assert_no_work()


@pytest.mark.parametrize(
    "updates,omit",
    [
        ({"iss": "https://other.example.invalid"}, ()),
        ({"aud": ["other-project"]}, ()),
        ({"aud": ["test-project", 42]}, ()),
        ({"exp": 1}, ()),
        ({"exp": "never"}, ()),
        ({"exp": True}, ()),
        ({"exp": 10**400}, ()),
        ({"iat": int(time.time()) + 3600}, ()),
        ({"nbf": int(time.time()) + 3600}, ()),
        ({"sub": {}}, ()),
        ({"jti": ""}, ()),
        ({"nonce": "id-token"}, ()),
        ({"sid": "id-token"}, ()),
        ({"auth_time": 1}, ()),
        ({"events": {}}, ()),
        ({}, ("exp",)),
        ({}, ("iat",)),
        ({}, ("aud",)),
        ({}, ("iss",)),
        ({}, ("sub",)),
        ({}, ("jti",)),
    ],
)
def test_bad_claims_do_not_fall_back_to_admin_cookie(bearer_api, updates, omit):
    api = bearer_api.api
    api.authenticate(["admin"])
    response = api.client.post(
        "/api/vendors",
        json={"name": "Forbidden"},
        headers=bearer_api.headers(updates=updates, omit=omit),
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert response.headers["cache-control"] == "no-store"
    api.assert_no_work()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Basic abc",
        "Bearer",
        "Bearer a b",
        "Bearer opaque",
        "Bearer not.a.jwt",
        "Bearer " + "a" * 16_385,
    ],
)
def test_malformed_headers_are_rejected_before_work(bearer_api, value):
    response = bearer_api.api.client.get(
        "/api/vendors", headers={"Authorization": value}
    )
    assert response.status_code == 401
    bearer_api.api.assert_no_work()
    assert bearer_api.requests == []


def test_duplicate_authorization_header_is_rejected(bearer_api):
    token = bearer_api.token()
    response = bearer_api.api.client.get(
        "/api/vendors", headers=[("Authorization", "Bearer " + token)] * 2
    )
    assert response.status_code == 401
    bearer_api.api.assert_no_work()
    assert bearer_api.requests == []


def test_disabled_bearer_auth_does_not_use_admin_cookie(security_api):
    security_api.authenticate(["admin"])
    response = security_api.client.get(
        "/api/vendors", headers={"Authorization": "Bearer opaque"}
    )
    assert response.status_code == 401
    security_api.assert_no_work()


@pytest.mark.parametrize(
    "role_data",
    [
        None,
        [],
        ["admin"],
        "admin",
        {"ADMIN": {"org": "domain"}},
        {"admin": {}},
        {"admin": []},
    ],
)
def test_wrong_or_missing_role_shapes_grant_no_access(bearer_api, role_data):
    claim = f"urn:zitadel:iam:org:project:{bearer_api.project}:roles"
    response = bearer_api.api.client.get(
        "/api/vendors",
        headers=bearer_api.headers(
            updates={
                claim: role_data,
                "roles": ["admin"],
                "urn:zitadel:iam:org:project:roles": {"admin": {"org": "domain"}},
            }
        ),
    )
    assert response.status_code == 403
    bearer_api.api.assert_no_work()


def test_other_project_roles_do_not_grant_access(bearer_api):
    response = bearer_api.api.client.get(
        "/api/vendors",
        headers=bearer_api.headers(
            roles=[],
            updates={
                "urn:zitadel:iam:org:project:other:roles": {"admin": {"org": "domain"}}
            },
        ),
    )
    assert response.status_code == 403
    bearer_api.api.assert_no_work()


def test_valid_viewer_token_takes_precedence_over_admin_cookie(bearer_api):
    bearer_api.api.authenticate(["admin"])
    response = bearer_api.api.client.post(
        "/api/vendors",
        json={"name": "Forbidden"},
        headers=bearer_api.headers(roles=["viewer"]),
    )
    assert response.status_code == 403
    bearer_api.api.assert_no_work()


def test_bearer_identity_does_not_leak_to_next_request_or_browser_page(bearer_api):
    api = bearer_api.api
    assert api.client.get("/auth/me", headers=bearer_api.headers()).status_code == 200
    assert api.client.get("/auth/me").status_code == 401
    page = api.client.get("/dashboard", headers=bearer_api.headers())
    assert page.status_code == 302
    assert page.headers["location"] == "/sign-in"
    api.assert_no_work()


def test_signature_and_untrusted_key_urls_are_not_accepted(bearer_api, signing_keys):
    bad = jwt.encode(
        {
            "alg": "RS256",
            "kid": signing_keys[0].kid,
            "jku": "https://attacker.invalid/keys",
        },
        {"iss": bearer_api.issuer},
        signing_keys[1],
    )
    response = bearer_api.api.client.get(
        "/api/vendors", headers={"Authorization": "Bearer " + bad}
    )
    assert response.status_code == 401
    assert len(bearer_api.requests) == 1
    bearer_api.api.assert_no_work()


def test_algorithm_confusion_is_rejected_without_fetching_keys(bearer_api):
    header = (
        base64.urlsafe_b64encode(
            json.dumps({"alg": "HS256", "kid": "api-key-one"}).encode()
        )
        .decode()
        .rstrip("=")
    )
    response = bearer_api.api.client.get(
        "/api/vendors", headers={"Authorization": f"Bearer {header}.e30.fake"}
    )
    assert response.status_code == 401
    assert bearer_api.requests == []
    bearer_api.api.assert_no_work()


def test_unknown_keys_are_rate_limited_and_rotation_is_supported(
    bearer_api, signing_keys
):
    api = bearer_api.api
    assert api.client.get("/auth/me", headers=bearer_api.headers()).status_code == 200
    bearer_api.keys = [signing_keys[1]]
    new_headers = bearer_api.headers(key=signing_keys[1])
    for _ in range(3):
        assert api.client.get("/auth/me", headers=new_headers).status_code == 401
    assert len(bearer_api.requests) == 1
    bearer_api.clock_offset = 11
    assert api.client.get("/auth/me", headers=new_headers).status_code == 200
    assert len(bearer_api.requests) == 2
    bearer_api.clock_offset = 312
    assert api.client.get("/auth/me", headers=new_headers).status_code == 200
    assert len(bearer_api.requests) == 3
    api.assert_no_work()


@pytest.mark.parametrize(
    "status,document",
    [
        (503, None),
        (200, {"keys": []}),
        (200, {"keys": "wrong"}),
        (200, {"keys": [{"kid": "api-key-one", "kty": "oct", "k": "secret"}]}),
    ],
)
def test_provider_errors_fail_closed_without_work(bearer_api, status, document):
    bearer_api.status, bearer_api.document = status, document
    response = bearer_api.api.client.get("/api/vendors", headers=bearer_api.headers())
    assert response.status_code == 503
    bearer_api.api.assert_no_work()


def test_cookie_requests_still_need_csrf_when_bearer_is_enabled(bearer_api):
    api = bearer_api.api
    api.authenticate(["analyst"])
    assert (
        api.client.post("/api/vendors", json={"name": "Forbidden"}).status_code == 403
    )
    api.assert_no_work()


def test_bearer_document_filter_keeps_vendor_context(bearer_api):
    api = bearer_api.api
    response = api.client.get(
        f"/api/vendors/{api.ids['vendor_a']}/documents",
        headers=bearer_api.headers(roles=["viewer"]),
    )
    assert response.status_code == 200
    assert [row["vendor_id"] for row in response.json()] == [api.ids["vendor_a"]]
