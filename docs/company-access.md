# Company and vendor access

Each app company maps to one explicitly registered ZITADEL Organization ID.
ZITADEL supplies Viewer, Analyst, and Admin roles for that project **in that
organization**. The app retains these grants separately; an Admin grant in one
organization cannot turn a Viewer into an Admin in another.

The user ID is ZITADEL's verified `sub`, not an email or a name. A user may have a
different vendor policy in each company. Logging in never claims existing data
or automatically registers a new company.

## Permissions

| User | Company vendor access |
| --- | --- |
| Viewer or Analyst with `all` | All vendors in the selected company |
| Viewer or Analyst with `assigned` | Only explicitly assigned vendors |
| Company Admin | All vendors and access controls in the selected company |
| Disabled company member | No company data, including with an Admin role |
| No project role, unknown company, or old cookie without company grants | No company data |

Vendor access is an additional restriction on the existing role permissions.
An assigned Viewer still cannot upload, create assessments, or ask the AI chat.
An assigned Analyst who creates a vendor receives an assignment to it in the
same transaction. This lets them work with the vendor they just created.

Lists, pagination, dashboard counts, assessment reports, documents, uploads,
ingestion, chat, and all MCP tools use the same company and vendor rules.
Unknown, foreign-company, and unassigned IDs return the same not-found response.
Denied vendor requests do not presign storage URLs, process documents, or call
Gemini. Documents and assessments inherit the vendor's company. Object storage
remains private, using globally unique vendor IDs in its keys.

## Register the initial company

Get the **Organization ID** for your company from ZITADEL Console. This is
different from `ZITADEL_PROJECT_ID`, an application ID, or a client ID.
Use the organization listed beneath the user's project role grants. The user's
home organization alone is not permission to access that company's data.

> **Check the ID before writing.** Signed in as an Admin, open `/auth/me`. The
> Organization ID is the key under `organization_roles`. Do not use `sub`, which
> is your user ID. Binding the workspace to the wrong ID locks everyone out of it.

The migration places every existing vendor in `Existing workspace`, with its
documents, chunks, assessments, findings, and IDs unchanged. The workspace is
locked until an operator explicitly links it to the correct organization.

For native Python, after applying migrations:

```bash
uv run --python 3.12 python scripts/ops/register_tenant.py \
  --org-id YOUR_ZITADEL_ORGANIZATION_ID --name "Your company" --legacy
```

The command previews the operation. Add `--write` to apply it. It reads your
private `.env` locally and does not print connection strings or credentials.
Re-running for the same organization is safe. It refuses to rebind existing
data to another organization or to merge two already registered companies.

For Compose, use the app container's internal database configuration:

```bash
docker compose --env-file .env exec -T app python scripts/ops/register_tenant.py \
  --org-id YOUR_ZITADEL_ORGANIZATION_ID --name "Your company" --legacy --write
```

Existing-workspace registration preserves the previous **all company vendors**
default for new members. To start with restricted users, add
`--default-access assigned` when registering. Admins can always assign access
after login. New, separate companies default to assigned vendors only.

### Fix a wrong binding

If the existing workspace was registered with the wrong ID (for example a user
`sub`), correct it directly. The guard on the old value makes the update a no-op
if anything else changed:

```sql
UPDATE tenants
SET zitadel_org_id = '<organization id>', updated_at = NOW()
WHERE id = '00000000-0000-0000-0000-000000000001'
  AND zitadel_org_id = '<wrong id>';
```

Then sign out and sign in again.

## Northflank rollout order

This is the order used to introduce companies on an existing deployment
(migration `2e1d4a7b9c60`). A new deployment applies all migrations with
`alembic upgrade head` and only needs the registration step. For any release
that includes a migration, run the migration job before the app is deployed.

1. Take a database backup and check the correct ZITADEL Organization ID.
2. Build the release image for the migration and utility jobs. Keep the
   existing application service on its current image during preparation.
3. Run the migration job with `alembic upgrade head` using that image and the
   production database. Check that it reports the latest revision.
4. Run the existing utility job using the same image and these values:

   ```dotenv
   OPS_TASK=register_tenant
   OPS_ZITADEL_ORG_ID=YOUR_ZITADEL_ORGANIZATION_ID
   OPS_TENANT_NAME=Your company
   OPS_LEGACY_TENANT=true
   OPS_WRITE=false
   ```

   Inspect the preview, then run it with `OPS_WRITE=true`. Use the Organization
   ID deliberately; this operator command does not validate ZITADEL grants itself.
5. Deploy the new app release. Sign out and sign in again; cookies from earlier
   releases containing only flattened roles cannot access company data.
6. Confirm your existing vendors and assessments are visible to the company's
   Admin. Use **Company access** to restrict a test Analyst to one vendor, and
   verify both the browser and MCP. Register additional companies only after
   the new app version is serving requests.

The database gives legacy inserts a default workspace ID so the previous app
can still create vendors between migration and deployment. The new app always
sets the company explicitly from verified access. Migration does not delete or
re-embed documents. Downgrade refuses to collapse multiple companies into a
shared workspace; restore a pre-migration backup if a rollback needs that.

## Manage users

Company Admins open `/admin/access` from the navigation. Users appear after their
first authorized request in that company. An Admin can also pre-register a
ZITADEL User ID and choose its vendor assignments before the user signs in.
This registration does **not** grant a ZITADEL role.

For each user, select **All company vendors** or **Assigned vendors only**,
choose vendors, and save. An empty assignment list gives a non-Admin no vendor
data. Unchecking **Company access enabled** blocks that user's company data.
An Admin cannot disable their own membership through this screen.

The company default affects only future members; existing users retain their
saved policy. Vendor policies are read on every request, so assignment removal
or disabling a membership takes effect without a new login. ZITADEL role changes
still require a refreshed browser login or a fresh bearer token.

This version supports assignments to individual users. Team/group assignments
and a company-level audit history are separate future features.

## More than one company

Browser users with several organization grants choose a company on `/companies`.
They can switch from their profile menu. Switching requires the session's CSRF
token, verifies the selected organization grant and membership, and rotates the
CSRF token. Request bodies cannot set a vendor's company.

API and MCP bearer requests select a company with:

```http
Authorization: Bearer YOUR_ACCESS_TOKEN
X-Organization-ID: YOUR_ZITADEL_ORGANIZATION_ID
```

The organization header only selects among the verified token's grants. It
cannot add a grant. With exactly one organization grant the header is optional;
with multiple grants it is required. The app never combines company roles.
Browser cookies ignore this header and use their chosen signed-session company.

## Verification

```bash
uv run --python 3.12 --with pytest pytest -q tests/security
```

Tests cover company separation, assigned vendors, Admin scope, new vendor
assignment, company switching and CSRF, disabled users, assignment revocation,
duplicate names across companies, foreign IDs, and MCP pagination and tools.
SQLite tests enable foreign keys. The CI Compose job also migrates a populated
PostgreSQL database from the previous revision, checks preserved records, compares
the migrated schema with the models, and tests composite assignment constraints.
Actual hosted ZITADEL grants and the Northflank cutover still need deployment
validation; automated tests use signed test tokens and mocked provider responses.
