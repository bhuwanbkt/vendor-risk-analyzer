# Operational and smoke scripts

This directory is intentionally separate from the Pytest suite.

## Automatic worker in the application service

The automatic worker and backfill command share storage and document
lifecycle helpers in `vendor_risk_analyzer.embeddings.repository`.

`EMBEDDING_WORKER_ENABLED` defaults to `true`. Set it to `false` to pause
automatic processing without opening database or Gemini clients for the worker.
The local `.env.example` disables it until a Gemini API key is configured.

Transient database failures during startup recovery or polling are retried
after `EMBEDDING_WORKER_POLL_SECONDS`. Stale-document recovery runs at startup
and every `EMBEDDING_WORKER_STALE_MINUTES`, so interrupted documents can be
recovered later without restarting the service. Processing renews the document
timestamp before each batch to keep active claims fresh. Database connections
are disposed even if embedding-client initialization or cleanup fails.
The worker runs in the FastAPI service and does not use a Northflank Job slot.

## Northflank Job 1 — database migrations

Keep the dedicated migration job command fixed:

```bash
uv run alembic upgrade head
```

Do not use this job for embeddings, assessments, smoke tests, or repairs.

## Northflank Job 2 — reusable utility job

Keep the utility job command fixed:

```bash
uv run python scripts/ops/run.py
```

Select the task through environment variables.

### Backfill embeddings

Dry run:

```text
OPS_TASK=backfill_embeddings
OPS_WRITE=false
OPS_LIMIT=50
OPS_DELAY_SECONDS=4
```

Optional document scope:

```text
OPS_DOCUMENT_ID=<document UUID>
```

Write mode:

```text
OPS_WRITE=true
```

### Apply deterministic risk policy

Dry run:

```text
OPS_TASK=apply_risk_policy
OPS_ASSESSMENT_ID=<assessment UUID>
OPS_WRITE=false
```

Write mode:

```text
OPS_WRITE=true
```

The dispatcher defaults to non-writing behavior unless `OPS_WRITE`
is explicitly truthy.

## Smoke scripts

`scripts/smoke/` contains cloud/integration verification utilities.
They are intended for the manual GitHub Actions integration workflow
or deliberate troubleshooting, not the reusable Northflank job.

## Pytest

Automated unit, security, and regression tests belong in `tests/`.
They run in GitHub Actions and do not consume Northflank Job slots.

## Dependency direction

Production application code under `src/vendor_risk_analyzer/`
must not import from `scripts/`.

Shared database and embedding lifecycle logic lives in the
application package. Operational scripts are thin clients that
import and invoke that application-owned logic.

This keeps deployment behavior independent from maintenance tooling
and lets CI catch accidental dependency inversions.
