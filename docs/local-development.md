# Local development

## Docker Compose

Install Docker Desktop on macOS/Windows, or Docker Engine plus Compose v2 on
Linux. Use Linux containers. Python is included in the app image.

MinIO's community distribution is source-only. Compose builds the pinned
[MinIO source release](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z)
using a separate Docker build stage; no Go installation is needed on your machine.
The first build downloads dependencies and can take several minutes. Later builds
reuse Docker's cache.

Copy `.env.local.example` to `.env.local`. Keep local credentials out of git.
Generate a unique session secret, for example:

```sh
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Paste it into `SESSION_SECRET`. Create your own ZITADEL project and OIDC application
using Authorization Code with S256 PKCE and authentication method `none`. The app
uses a client ID and does not send a client secret. Enable Development mode,
register `http://localhost:8000/auth/callback` and the post-logout URI
`http://localhost:8000/`, and fill in `ZITADEL_ISSUER`, `ZITADEL_CLIENT_ID` and
`ZITADEL_PROJECT_ID`. Create `viewer`, `analyst` and `admin` project roles, enable
**Assert Roles on Authentication**, and assign a role to each user.
See [ZITADEL application settings](https://zitadel.com/docs/guides/manage/console/applications-overview).

Add your Gemini API key and enable `EMBEDDING_WORKER_ENABLED=true` for automatic
embeddings. Configure embedding and risk model names available to your account.
Keep the configured embedding dimension at 768 to match the database schema.
With the worker disabled, ingested documents remain `embedding_pending`.
ZITADEL and Gemini are external services; Compose supplies the app, database and
storage. There is no authentication bypass in local mode.

From the repository root:

```sh
docker compose --env-file .env.local config --quiet
docker compose --env-file .env.local up --build -d
docker compose --env-file .env.local ps -a
docker compose --env-file .env.local logs -f app
```

Use `--env-file .env.local` on every Compose command. The file supplies both
Compose port/credential substitutions and application environment variables.
Compose replaces the application's database/storage hostnames with internal
service names, while signed upload URLs use a browser-accessible localhost host.

| Service | Default local address |
| --- | --- |
| Application | http://localhost:8000 |
| Liveness | http://localhost:8000/health |
| Database readiness | http://localhost:8000/ready |
| PostgreSQL | localhost:5433 |
| MinIO S3 API | http://localhost:9000 |
| MinIO console | http://localhost:9001 |

All published ports bind to the local machine's loopback interface. Use
`localhost` consistently in the browser, callbacks and storage URLs. Log into the
MinIO console with `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` from `.env.local`.
The `vendor-documents` bucket is private.

The database initializer enables pgvector, `migrate` applies Alembic migrations,
and `storage-init` creates the bucket. Their completed exit status is expected.
The app starts after both jobs succeed. PostgreSQL and storage use named volumes.
Stop the stack without deleting its data:

```sh
docker compose --env-file .env.local down
```

Restart with the same `up --build -d` command after pulling code or editing
settings. Changing database credentials does not change an existing PostgreSQL
volume's password. The example database password is URL-safe; use URL-safe
characters if changing `POSTGRES_PASSWORD` in this local Compose setup.

If a port is occupied, edit its `*_PORT` setting. Update callback/logout URLs and
their ZITADEL registrations when changing `APP_PORT`. For the pip setup below,
also update the host `DATABASE_URL`/storage endpoints and credentials to match.
Inspect failed startup jobs with:

```sh
docker compose --env-file .env.local logs migrate storage-init app
```

## Run Python with pip

Use Python 3.12, matching the container and CI. `requirements.txt` is exported from
`uv.lock` and pins application dependencies, including an editable installation of
this repository. Run commands from the repository root.

Prepare `.env.local` as above, then start only the supporting services:

```sh
docker compose --env-file .env.local up -d db minio
docker compose --env-file .env.local run --rm storage-init
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
python -m dotenv -f .env.local run -- alembic upgrade head
python -m uvicorn vendor_risk_analyzer.main:app --host 127.0.0.1 --port 8000 --env-file .env.local
```

On Windows PowerShell, create the environment with `py -3.12 -m venv .venv` and
activate it with `.venv\Scripts\Activate.ps1`. The remaining `python` commands are
the same. If the Compose app was already started, stop it with
`docker compose --env-file .env.local stop app` before binding Python to port 8000.

Alembic reads `DATABASE_URL` from the process environment, so run it through
`python -m dotenv` as shown. Uvicorn loads `.env.local` for the application.
When changing the application port, change the Uvicorn `--port` argument too.

## Maintainer checks

Regenerate requirements after changing the lock file:

```sh
uv export --frozen --no-dev --no-hashes --format requirements-txt --output-file requirements.txt
uv run --python 3.12 --with pytest pytest -q
```

The CI Compose smoke script uses signed test identities and a disposable database
to verify RBAC, private signed uploads, internal downloads, ingestion and
persistence. It does not test actual ZITADEL login or Gemini output. Check those
manually with your own development accounts and sample vendor documents.
