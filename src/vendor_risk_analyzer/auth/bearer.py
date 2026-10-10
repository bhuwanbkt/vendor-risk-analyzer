"""Opt-in JWT access-token authentication for API clients.

Only the configured ZITADEL issuer's public keys and project audience are trusted.
Browser pages continue to use signed sessions.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import time
from functools import lru_cache

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import RSAKey
from joserfc.jwt import JWTClaimsRegistry
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from vendor_risk_analyzer.config import get_settings


class InvalidBearerToken(Exception):
    pass


class BearerProviderUnavailable(Exception):
    pass


class BearerTokenVerifier:
    def __init__(self, issuer: str, project_id: str):
        self.issuer = issuer.rstrip("/")
        self.project_id = project_id
        self._keys: dict[str, RSAKey] = {}
        self._expires_at = 0.0
        self._refresh_after = 0.0
        self._lock = asyncio.Lock()

    async def _refresh_keys(self):
        keys_url = self.issuer + "/oauth/v2/keys"
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                async with client.stream("GET", keys_url) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 256 * 1024:
                            raise ValueError("Key response exceeds limit")
            document = json.loads(body)
            entries = document.get("keys") if isinstance(document, dict) else None
            if not isinstance(entries, list) or not 0 < len(entries) <= 100:
                raise ValueError("Invalid key set")
            keys = {}
            for entry in entries:
                if (
                    not isinstance(entry, dict)
                    or entry.get("kty") != "RSA"
                    or entry.get("use", "sig") != "sig"
                    or entry.get("alg", "RS256") != "RS256"
                    or "d" in entry
                ):
                    continue
                kid = entry.get("kid")
                if not isinstance(kid, str) or not 0 < len(kid) <= 200 or kid in keys:
                    raise ValueError("Invalid key identifier")
                keys[kid] = RSAKey.import_key(entry)
            if not keys:
                raise ValueError("No usable signing keys")
        except (
            httpx.HTTPError,
            ValueError,
            TypeError,
            KeyError,
            RecursionError,
            JoseError,
        ) as exc:
            raise BearerProviderUnavailable() from exc
        self._keys = keys
        self._expires_at = time.monotonic() + 300

    async def _get_key(self, kid: str):
        now = time.monotonic()
        if now < self._expires_at and kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            now = time.monotonic()
            if now < self._expires_at and kid in self._keys:
                return self._keys[kid]
            if now >= self._refresh_after:
                self._refresh_after = now + 10
                await self._refresh_keys()
            if time.monotonic() >= self._expires_at:
                raise BearerProviderUnavailable()
            if kid not in self._keys:
                raise InvalidBearerToken()
            return self._keys[kid]

    async def verify(self, value: str) -> dict:
        try:
            if not value.isascii() or len(value) > 16_384 or len(value.split(".")) != 3:
                raise InvalidBearerToken()
            encoded_header = value.split(".")[0]
            header = json.loads(
                base64.urlsafe_b64decode(
                    encoded_header + "=" * (-len(encoded_header) % 4)
                )
            )
            if not isinstance(header, dict) or header.get("alg") != "RS256":
                raise InvalidBearerToken()
            kid = header.get("kid")
            if not isinstance(kid, str) or not 0 < len(kid) <= 200:
                raise InvalidBearerToken()
            key = await self._get_key(kid)
            token = jwt.decode(value, key, algorithms=["RS256"])
            claims = token.claims
            # ZITADEL JWT access tokens have jti; its ID tokens do not.
            if token.header.get("typ", "JWT") not in {"JWT", "at+jwt"} or any(
                name in claims for name in ("nonce", "sid", "auth_time", "events")
            ):
                raise InvalidBearerToken()
            for name in ("sub", "jti", "iss"):
                if not isinstance(claims.get(name), str) or not claims[name].strip():
                    raise InvalidBearerToken()
            audience = claims.get("aud")
            if not (
                isinstance(audience, str)
                and audience
                or isinstance(audience, list)
                and audience
                and all(isinstance(item, str) and item for item in audience)
            ):
                raise InvalidBearerToken()
            for name in ("exp", "iat", "nbf"):
                if name not in claims and name == "nbf":
                    continue
                number = claims.get(name)
                if (
                    isinstance(number, bool)
                    or not isinstance(number, (int, float))
                    or not math.isfinite(number)
                ):
                    raise InvalidBearerToken()
            JWTClaimsRegistry(
                leeway=30,
                iss={"essential": True, "value": self.issuer},
                aud={"essential": True, "value": self.project_id},
                exp={"essential": True},
                iat={"essential": True},
            ).validate(claims)
            if claims["exp"] <= claims["iat"]:
                raise InvalidBearerToken()
            role_data = claims.get(
                f"urn:zitadel:iam:org:project:{self.project_id}:roles", {}
            )
            roles = []
            if isinstance(role_data, dict):
                for role, grants in role_data.items():
                    if (
                        isinstance(role, str)
                        and isinstance(grants, dict)
                        and grants
                        and all(
                            isinstance(org, str)
                            and org
                            and isinstance(domain, str)
                            and domain
                            for org, domain in grants.items()
                        )
                    ):
                        roles.append(role)
            return {"sub": claims["sub"], "roles": roles}
        except (
            JoseError,
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            RecursionError,
        ) as exc:
            raise InvalidBearerToken() from exc


@lru_cache(maxsize=8)
def get_bearer_verifier(issuer: str, project_id: str):
    return BearerTokenVerifier(issuer, project_id)


class ApiBearerAuthenticationMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (
            path.startswith("/api/") or path == "/auth/me"
        ):
            return await self.app(scope, receive, send)
        scope.pop("api_bearer_authenticated", None)
        scope.pop("api_bearer_user", None)
        values = Headers(scope=scope).getlist("authorization")
        if not values:
            return await self.app(scope, receive, send)
        response = None
        try:
            settings = get_settings()
            if not settings.api_bearer_enabled or len(values) != 1:
                raise InvalidBearerToken()
            parts = values[0].split()
            if len(parts) != 2 or parts[0].lower() != "bearer":
                raise InvalidBearerToken()
            verifier = get_bearer_verifier(
                settings.zitadel_issuer, settings.zitadel_project_id
            )
            scope["api_bearer_user"] = await verifier.verify(parts[1])
            scope["api_bearer_authenticated"] = True
        except InvalidBearerToken:
            response = JSONResponse(
                {"detail": "Invalid or unsupported access token"},
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Bearer realm="vendor-risk-analyzer", error="invalid_token"',
                    "Cache-Control": "no-store",
                },
            )
        except BearerProviderUnavailable:
            response = JSONResponse(
                {"detail": "Authentication provider unavailable"},
                status_code=503,
                headers={"Cache-Control": "no-store", "Retry-After": "10"},
            )
        if response is not None:
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)
