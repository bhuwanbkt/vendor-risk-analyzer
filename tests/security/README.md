# HTTP security tests

Run these tests with the repository's locked application dependencies:

```sh
uv run --python 3.12 --with pytest pytest -q tests/security
```

The existing CI workflow collects this suite automatically. Tests send HTTP requests
through the real FastAPI routes, signed session middleware, role dependencies, CSRF
checks, and rendered pages. Auth dependencies are never overridden. ORM queries run
against a seeded in-memory SQLite database with two vendors. Assessment creation,
ingestion, embeddings, chat, and storage services are mocked at their route boundaries.
The embedding worker is mocked, and outbound socket connections fail the tests.

| Access | Viewer | Analyst | Admin | No assigned application role |
| --- | --- | --- | --- | --- |
| Vendor, document, assessment APIs and data pages | Allowed | Allowed | Allowed | Forbidden |
| Vendor creation, upload, ingest, assessment creation, chat | Forbidden | Allowed with CSRF | Allowed with CSRF | Forbidden |
| Chat page | Forbidden | Allowed | Allowed | Forbidden |
| System page | Forbidden | Forbidden | Allowed | Forbidden |
| Own profile and `/auth/me` | Allowed | Allowed | Allowed | Access information page and own identity |

Anonymous API requests return 401; private page requests redirect to login. Missing,
unknown, malformed, and incorrectly cased role claims grant no application access.
The public `/sign-in` screen renders without database or provider work. Roleless
accounts receive a separate access message and account-switch form, including on
direct private pages (403), with no private navigation or data. Their `/profile`
page uses the same access screen. Account details are escaped in HTML.
Tests also cover missing or invalid CSRF tokens, expired or modified session cookies,
secure cookie attributes, and rejection before database or external-service work.

OIDC flow tests exercise the real Authlib client, authorization state, S256 PKCE,
nonce checks, signed ID tokens, issuer/audience/signature/expiry validation, and the
application callback. Provider discovery, token, JWKS, and UserInfo HTTP responses
use an in-process HTTPX transport with test-only RSA keys. No identity-provider
account, credentials, or network connections are needed. Tests reject missing ID
tokens and invalid or mismatched UserInfo, grant no access for malformed project
roles, replace the old identity during a new login, rotate CSRF tokens after login,
and verify local logout plus the encoded ZITADEL client/return-URI/account redirect.
Sign-in requests force `prompt=login`; entered login names are trimmed, bounded,
and encoded correctly. Logout ignores caller-supplied account hints and uses the
authenticated provider login name when present. A simulated provider return to the
app root opens the public form without starting OIDC again.

Vendor boundaries cover document list filters, vendor/document ID matching before
completion or ingestion, vendor-specific upload keys, assessment finding separation,
and explicit vendor context passed to chat. Application roles currently have access
to all vendors: this suite does not assert per-user or tenant ownership, which the
current schema does not model. SQLite does not validate PostgreSQL/pgvector search.
The OIDC tests do not exercise ZITADEL's hosted login UI, its browser SSO cookies,
or actual provider session termination. Those behaviors, object storage, LLMs, and
Northflank deployment still require live integration validation.
