# Row-level security

Company isolation is enforced twice:

1. **In the application.** Every query filters by the caller's company and
   vendor access (`AccessContext.vendor_filter()`).
2. **In PostgreSQL.** Row-level security (RLS) policies hide other companies'
   rows from the app's database role, so a query that forgot its company
   filter returns nothing instead of another company's data.

The second layer only works when the app connects with a role that is subject
to RLS. This page explains how it works and how to switch it on.

## How it works

Migration `e3b7d1f5a9c4` adds a `company_isolation` policy to every
company-owned table:

| Tables | A row belongs to the company when |
| --- | --- |
| `vendors`, `tenant_memberships`, `vendor_assignments`, `chat_conversations`, `audit_events`, `llm_usage_events` | its `tenant_id` is the current company |
| `documents`, `assessments` | their vendor belongs to the current company |
| `document_elements`, `document_chunks` | their document's vendor does |
| `findings` | their assessment's vendor does |
| `chat_messages` | their conversation does |

The current company comes from a transaction-local setting. When a request has
resolved the caller's company, the app marks its session
(`bind_tenant` in [db/tenant_context.py](../src/vendor_risk_analyzer/db/tenant_context.py)),
and every transaction on that session starts with:

```sql
SELECT set_config('app.tenant_id', '<company id>', true),
       set_config('app.rls_bypass', 'off', true);
```

`true` makes both settings last only for the current transaction, so they are
safe with connection poolers (including Neon's pooled endpoint) and never leak
to the next request. A session that was never marked has no company, which
matches no rows.

Background work spans every company: the embedding worker, the assessment
worker's queue, and the operations scripts set `app.rls_bypass = 'on'`. An
assessment job then narrows its analysis session to the vendor's company, so
retrieval, saving, and scoring for one company cannot touch another's data.

Not covered: `tenants` (looked up by organization ID before a company is
known; it holds only names and settings) and `rate_limit_buckets` (keys already
include the company).

## Who is affected

PostgreSQL skips RLS for superusers, roles with `BYPASSRLS`, and a table's
owner. The policies do not use `FORCE ROW LEVEL SECURITY`, so the role that
owns the tables (the one that runs migrations) keeps full access. That keeps
migrations, the utility job, and the Neon SQL Editor working as before.

On Neon, roles created in the Console belong to `neon_superuser`, which has
`BYPASSRLS`. The app therefore needs its own role, created with SQL.

The admin **System** page shows whether RLS is enforced for the app's current
role, and why not if it is not.

## Switch it on

### 1. Apply the migration

Run the migration job as usual. Nothing changes yet: the app still connects
as the owner, which the policies do not affect.

### 2. Create the app role

In the Neon **SQL Editor** (connected as the owner, for example `neondb_owner`),
choose a long random password and run:

```sql
CREATE ROLE vendor_risk_app LOGIN PASSWORD '<long random password>';

GRANT USAGE ON SCHEMA public TO vendor_risk_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO vendor_risk_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vendor_risk_app;

-- Tables created by future migrations (run as the owner that runs migrations).
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vendor_risk_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO vendor_risk_app;
```

The role cannot create or alter tables, and it has no `BYPASSRLS`.

### 3. Point only the app at the new role

In Northflank, change `DATABASE_URL` for the **application service** only: the
same host and database, with user `vendor_risk_app` and the new password.
Keep the owner connection string on the **migration** and **utility** jobs;
they need to change the schema and work across companies.

Restart the app, then check:

1. **System** page: *Row-level security: Enforced for role vendor_risk_app*.
2. Vendors, a report, an export, and a chat answer all work.
3. Upload a small document and run an assessment (both workers).

**To roll back,** set the app's `DATABASE_URL` back to the owner connection
string. Policies stay in place but no longer apply.

## Limits

- **Protects against forgotten filters, not against arbitrary SQL.** The app's
  role can set `app.rls_bypass` itself (the workers need it), so code that runs
  attacker-controlled SQL could bypass the policies. The app only uses
  parameterized ORM queries.
- **One extra statement per transaction** (`set_config`) for marked sessions.
- **Local Compose** connects as the `postgres`-image superuser, so RLS is not
  enforced locally. CI checks the policies with a restricted role
  ([tests/rls_smoke.py](../tests/rls_smoke.py)).
- A new company-owned table needs a policy in a migration;
  `tests/test_tenant_context.py` fails until it is added or explicitly listed
  as unprotected.
