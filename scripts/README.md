# Operational and smoke scripts

This directory is intentionally separate from the Pytest suite.

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
