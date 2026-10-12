"""Security response headers for every HTTP response.

The pages load only same-origin scripts and styles (no inline code), so the
Content-Security-Policy can stay strict. Two external origins are allowed:

- object storage, which the browser uploads to with signed PUT URLs
- the ZITADEL issuer, which the sign-in form redirects to
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vendor_risk_analyzer.config import Settings, get_settings


def _origin(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def storage_origins(settings: Settings) -> list[str]:
    endpoint = (
        settings.object_storage_public_endpoint
        or settings.object_storage_endpoint
    )
    origin = _origin(endpoint)
    if origin is None:
        return []
    origins = [origin]
    # Virtual-hosted addressing puts the bucket in the hostname.
    if settings.object_storage_addressing_style != "path":
        scheme, netloc = origin.split("://", 1)
        origins.append(
            f"{scheme}://{settings.object_storage_bucket}.{netloc}"
        )
    return origins


def build_content_security_policy(settings: Settings) -> str:
    connect = ["'self'", *storage_origins(settings)]
    form_action = ["'self'"]
    issuer = _origin(settings.zitadel_issuer)
    if issuer:
        form_action.append(issuer)

    directives = {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
        "img-src": ["'self'", "data:"],
        "font-src": ["'self'"],
        "connect-src": connect,
        "form-action": form_action,
        "frame-ancestors": ["'none'"],
        "base-uri": ["'self'"],
        "object-src": ["'none'"],
    }
    return "; ".join(
        f"{name} {' '.join(dict.fromkeys(values))}"
        for name, values in directives.items()
    )


def build_security_headers(settings: Settings) -> list[tuple[bytes, bytes]]:
    headers = {
        "content-security-policy": build_content_security_policy(settings),
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "strict-origin-when-cross-origin",
        "permissions-policy": "camera=(), microphone=(), geolocation=()",
    }
    # Secure cookies mean the app is served over HTTPS.
    if settings.session_cookie_secure:
        headers["strict-transport-security"] = "max-age=31536000"
    return [
        (name.encode("latin-1"), value.encode("latin-1"))
        for name, value in headers.items()
    ]


class SecurityHeadersMiddleware:
    """Add security headers unless a response already set its own value."""

    def __init__(self, app: ASGIApp, settings: Settings | None = None):
        self.app = app
        self.headers = build_security_headers(settings or get_settings())

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message: Message):
            if message["type"] == "http.response.start":
                raw = list(message.get("headers", []))
                present = {name.lower() for name, _ in raw}
                raw.extend(
                    (name, value)
                    for name, value in self.headers
                    if name not in present
                )
                message["headers"] = raw
            await send(message)

        return await self.app(scope, receive, send_with_headers)
