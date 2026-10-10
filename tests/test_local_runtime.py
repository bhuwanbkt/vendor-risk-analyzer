from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from vendor_risk_analyzer.config import Settings


def settings(**updates):
    values = {
        "app_env": "development",
        "session_cookie_secure": True,
        "api_bearer_enabled": False,
        "database_url": "postgresql://test:test@localhost/test",
        "zitadel_issuer": "https://identity.example.invalid",
        "zitadel_client_id": "client",
        "zitadel_project_id": "project",
        "zitadel_redirect_uri": "http://localhost:8000/auth/callback",
        "zitadel_post_logout_uri": "http://localhost:8000/",
        "session_secret": "test-secret",
        "object_storage_endpoint": "http://minio:9000",
        "object_storage_region": "us-east-1",
        "object_storage_access_key_id": "test-access",
        "object_storage_secret_access_key": "test-secret",
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


@pytest.fixture
def storage(monkeypatch):
    from vendor_risk_analyzer.config import get_settings

    config = settings()
    for name, value in config.model_dump().items():
        if value is not None:
            monkeypatch.setenv(name.upper(), str(value))
    get_settings.cache_clear()
    from vendor_risk_analyzer.storage import client

    monkeypatch.setattr(client, "settings", config)
    client.get_storage_client.cache_clear()
    client.get_upload_client.cache_clear()
    yield client
    client.get_storage_client.cache_clear()
    client.get_upload_client.cache_clear()
    get_settings.cache_clear()


def test_http_development_cookie_survives_next_browser_request():
    config = settings(session_cookie_secure=False)

    async def login(request):
        request.session["user"] = "local-test"
        return JSONResponse({"status": "set"})

    async def me(request):
        return JSONResponse({"user": request.session.get("user")})

    app = Starlette(routes=[Route("/login", login), Route("/me", me)])
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.session_secret,
        https_only=config.session_cookie_secure,
    )
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/login")
        assert "secure" not in response.headers["set-cookie"].lower()
        assert client.get("/me").json() == {"user": "local-test"}


def test_secure_cookies_remain_the_default():
    assert settings().session_cookie_secure is True


@pytest.mark.parametrize(
    "updates",
    [
        {"app_env": "production"},
        {"zitadel_redirect_uri": "https://deployed.example/auth/callback"},
        {"zitadel_post_logout_uri": "https://deployed.example/"},
    ],
)
def test_nonsecure_cookies_are_refused_outside_local_development(updates):
    with pytest.raises(ValidationError, match="localhost"):
        settings(session_cookie_secure=False, **updates)


@pytest.mark.parametrize(
    "updates",
    [
        {"zitadel_issuer": "http://identity.example"},
        {"zitadel_issuer": "https://user:password@identity.example"},
        {"zitadel_issuer": "https://identity.example?redirect=elsewhere"},
        {"zitadel_project_id": ""},
    ],
)
def test_bearer_configuration_requires_trusted_https_issuer(updates):
    with pytest.raises(ValidationError, match="HTTPS"):
        settings(api_bearer_enabled=True, **updates)


def test_presigned_browser_upload_uses_public_endpoint_but_download_uses_internal(
    monkeypatch, storage
):
    monkeypatch.setattr(
        storage,
        "settings",
        settings(
            object_storage_public_endpoint="http://localhost:9000",
            object_storage_addressing_style="path",
        ),
    )
    storage.get_storage_client.cache_clear()
    storage.get_upload_client.cache_clear()
    try:
        url = storage.generate_upload_url(
            object_key="vendors/test/example.txt", content_type="text/plain"
        )
        parts = urlsplit(url)
        assert parts.netloc == "localhost:9000"
        assert parts.path == "/vendor-documents/vendors/test/example.txt"
        assert parse_qs(parts.query)["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
        assert storage.get_storage_client().meta.endpoint_url == "http://minio:9000"
    finally:
        storage.get_storage_client.cache_clear()
        storage.get_upload_client.cache_clear()


def test_cloud_storage_reuses_one_client_when_no_public_override(monkeypatch, storage):
    monkeypatch.setattr(storage, "settings", settings())
    storage.get_storage_client.cache_clear()
    storage.get_upload_client.cache_clear()
    try:
        assert storage.get_upload_client() is storage.get_storage_client()
    finally:
        storage.get_storage_client.cache_clear()
        storage.get_upload_client.cache_clear()
