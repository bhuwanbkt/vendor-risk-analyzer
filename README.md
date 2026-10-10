# Vendor Risk Analyzer

A FastAPI application for reviewing vendor security documents, generating risk
assessments, and asking questions about vendor evidence. The application uses
PostgreSQL/pgvector, object storage, Gemini, and ZITADEL authentication.

## Authentication and access

Configure the ZITADEL settings shown in [.env.example](.env.example). Register
`ZITADEL_REDIRECT_URI` as the application's callback URI and
`ZITADEL_POST_LOGOUT_URI` as its post-logout URI in ZITADEL. Both must match the
deployed URLs exactly. Use the project identified by `ZITADEL_PROJECT_ID`, create
the exact role keys below, assign a role to each user, and enable **Assert Roles on
Authentication** so UserInfo includes the project's roles.

| Application role | Access |
| --- | --- |
| `viewer` | Read vendors, documents, and assessments. |
| `analyst` | Viewer access plus vendor creation, document upload/processing, assessments, and AI chat. |
| `admin` | Analyst access plus `/admin/system`. This does not grant ZITADEL administration rights. |
| No app role | Own profile and `/auth/me`; protected data and system pages are forbidden. |

Application roles currently apply to all vendors. Per-user vendor ownership and
tenant separation are not implemented.

The callback requires an Authlib-validated ID token and matching UserInfo subject
before creating a session. Only this application's project role claim is used.
Starting a new login clears the prior local identity, and successful login creates
a fresh CSRF token. Access and ID tokens are not stored in the application session.
After changing a user's roles, log out and sign in again to refresh the app session.

Use **Logout** to clear the app session and redirect the browser to ZITADEL's end
session endpoint. The app supplies its client ID and encoded configured return URI.
Normal ZITADEL single sign-on still applies: closing a tab does not log you out.
For an isolated Chrome Incognito test, close every Incognito window before opening
a fresh one. Browser authentication requires HTTPS because app cookies are Secure.

## Tests

Use Python 3.12, matching the production container and CI:

```sh
uv sync --python 3.12 --frozen --no-dev
uv run --python 3.12 --with pytest pytest -q
```

To run only security tests:

```sh
uv run --python 3.12 --with pytest pytest -q tests/security
```

These tests run without production credentials or external services. See
[the security test guide](tests/security/README.md) for coverage and limitations.
GitHub Actions compiles the sources and runs the full suite with coverage on pull
requests and pushes to `main`.
