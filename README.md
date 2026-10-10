# Vendor Risk Analyzer

A FastAPI application for reviewing vendor security documents, generating risk
assessments, and asking questions about vendor evidence. The application uses
PostgreSQL/pgvector, object storage, Gemini, and ZITADEL authentication.

## Run locally

Use Docker Desktop on macOS/Windows, or Docker Engine with Compose v2 on Linux.
The Compose stack runs the app, PostgreSQL with pgvector, and private MinIO object
storage. It applies migrations and creates the storage bucket before starting the
app. Data persists across restarts.

```sh
git clone https://github.com/bhuwanbkt/vendor-risk-analyzer.git
cd vendor-risk-analyzer
cp .env.local.example .env.local
```

Edit `.env.local`: replace `SESSION_SECRET` and the ZITADEL issuer/client/project
placeholders with your own settings. For AI features, add `GEMINI_API_KEY` and set
`EMBEDDING_WORKER_ENABLED=true`. Register these exact local URLs in a separate
ZITADEL development application:

- Callback: `http://localhost:8000/auth/callback`
- After logout: `http://localhost:8000/`

Enable ZITADEL development mode for HTTP callbacks, use Authorization Code with
S256 PKCE and token endpoint authentication `none`, and assign users the lowercase
project roles described below. This app does not configure a client secret.

```sh
docker compose --env-file .env.local up --build -d
docker compose --env-file .env.local logs -f app
```

Open [http://localhost:8000](http://localhost:8000). The public sign-in page and
health checks can start with the example settings; actual sign-in requires your
ZITADEL configuration, and embeddings/assessments/chat require Gemini. These
external services are not included in Compose, so the complete app is not offline.

See [the local setup guide](docs/local-development.md) for pip installation using
`requirements.txt`, service ports, stopping the stack, and troubleshooting. Local
HTTP cookies are permitted only in development with localhost callback/logout
URLs. Hosted deployments keep Secure cookies by default.

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
| No app role | Access information and sign-in screen, plus own identity at `/auth/me`; protected data and system pages are forbidden. |

Application roles currently apply to all vendors. Per-user vendor ownership and
tenant separation are not implemented.

The callback requires an Authlib-validated ID token and matching UserInfo subject
before creating a session. Only this application's project role claim is used.
Starting a new login clears the prior local identity, and successful login creates
a fresh CSRF token. Access and ID tokens are not stored in the application session.
After changing a user's roles, log out and sign in again to refresh the app session.

Anonymous visitors open `/sign-in`, a public introduction and an email/username
form. Submitting the form starts ZITADEL authentication with `prompt=login` and
the entered account as `login_hint`; passwords remain on ZITADEL's hosted page.
Accounts without a recognized app role see an access message and the same form
for signing in with another account. They see no app navigation, data, or controls.
Direct private page requests still return 403 before reading data.

Use the visible **Sign out** button to clear the app session and redirect the
browser to ZITADEL's end-session endpoint. The app supplies its client ID and
encoded configured return URI, plus the authenticated `preferred_username` as
`logout_hint` when available (supported by ZITADEL Login UI V2). With the configured
return URI pointing to the app root, logout returns to `/sign-in` without
automatically starting another login. The existing root return URI can remain
registered; no ZITADEL URL change is needed for this flow. See
[ZITADEL's endpoint parameters](https://zitadel.com/docs/apis/openidoauth/endpoints)
for hosted login/logout behavior.

Explicit sign-in now requests fresh authentication. Closing a tab does not log you out.
For an isolated Chrome Incognito test, close every Incognito window before opening
a fresh one. Hosted browser authentication requires HTTPS because app cookies are
Secure; the documented localhost development setup has an explicit exception.

To validate after deployment: sign out and confirm the public sign-in page opens;
sign in with another account and check its permissions; sign in with an account
that has no app role and confirm only the access message/form appear. ZITADEL
controls the hosted login/logout UI, so provider account selection and session
termination still need a live browser check.

## External API clients

JWT bearer access tokens are supported when `API_BEARER_ENABLED=true`. This is off
by default. Tokens must be signed by the configured ZITADEL issuer, target this
project, and contain its project-specific roles; the existing role permissions
apply. Cookie requests still require CSRF for writes. See
[API authentication](docs/api-authentication.md) for provider setup and examples.
This provides API authentication for external clients; an MCP server and tools are
not implemented yet.

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

A second CI job builds and starts Compose, checks PostgreSQL migrations and private
MinIO uploads/ingestion, and installs `requirements.txt` in a clean pip environment.
It uses disposable test identities and does not contact ZITADEL or Gemini.
