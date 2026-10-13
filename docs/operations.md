# Operations runbooks

Two drills that make database changes and recovery routine instead of risky.
Both use Neon features available on the free plan and do not touch the
production branch unless a step says so.

- [Rehearse a migration on a copy of production](#rehearse-a-migration-on-a-copy-of-production)
- [Restore drill](#restore-drill)
- [Free-plan limits that affect recovery](#free-plan-limits-that-affect-recovery)

Neon's Console labels change occasionally; if a button is named differently,
the [branching](https://neon.com/docs/manage/branches) and
[restore](https://neon.com/docs/introduction/branch-restore) docs have the
current wording.

## Rehearse a migration on a copy of production

**When:** before every release that adds a file under `migrations/versions/`.

A Neon branch is an instant copy-on-write copy of the database. Running the
migration there first shows whether it applies cleanly to real data, how long
it takes, and whether it can be rolled back, without risk to production.

### 1. Create the branch

1. In the Neon Console, open **Branches** and click **New branch**.
2. Parent branch: your production branch (usually `main`).
3. Name: `migration-rehearsal`.
4. Include **Current data**, then **Create**.
5. Click **Connect**, choose the new branch, and copy its connection string.

### 2. Record the starting point

In the Neon **SQL Editor** with the rehearsal branch selected:

```sql
SELECT version_num FROM alembic_version;

SELECT 'vendors' AS table_name, count(*) FROM vendors
UNION ALL SELECT 'documents', count(*) FROM documents
UNION ALL SELECT 'document_chunks', count(*) FROM document_chunks
UNION ALL SELECT 'assessments', count(*) FROM assessments
UNION ALL SELECT 'findings', count(*) FROM findings;
```

Keep the output; the counts must not change.

### 3. Upgrade, roll back, upgrade again

From the repository on the release commit, using the branch connection string:

```bash
export DATABASE_URL='<rehearsal branch connection string>'

time uv run alembic upgrade head      # apply; note how long it took
uv run alembic current                # shows the new head revision
uv run alembic downgrade -1           # the rollback must also work
uv run alembic upgrade head           # and apply again
unset DATABASE_URL
```

If a downgrade is deliberately impossible (it says so in the migration), skip
that line and plan to restore from a branch instead of downgrading.

### 4. Check the result

Run the step 2 queries again. The revision must be the new head and the row
counts must match. Optionally start the app against the branch to click
through a vendor, a report, and a chat:

```bash
DATABASE_URL='<rehearsal branch connection string>' \
  uv run uvicorn vendor_risk_analyzer.main:app --port 8000
```

This uses your local `.env` for everything else. Leave
`EMBEDDING_WORKER_ENABLED` and `ASSESSMENT_WORKER_ENABLED` false if you do not
want it to spend Gemini quota.

### 5. Clean up and release

1. Delete the branch: **Branches**, select it, **⋮**, **Delete**.
2. Release as usual: build the migration job from the new `main`, run it, then
   deploy the app (see [Deployment and operations](../README.md#deployment-and-operations)).

### Rehearsal record

Copy this into the pull request or your notes:

| Item | Result |
| --- | --- |
| Release / commit | |
| Revision before → after | |
| Upgrade time | |
| Downgrade and re-upgrade | worked / not supported |
| Row counts unchanged | yes / no |
| App checked against branch | yes / no |

## Restore drill

**When:** once now, then every few months or after major changes.

The aim is to measure two numbers you would otherwise guess during an outage:

- **RPO (recovery point objective):** how much recent data a restore loses.
- **RTO (recovery time objective):** how long recovery takes from deciding to
  restore until the app works again.

### Safe drill: restore into a new branch

This rehearses the recovery without touching production.

1. Note the current time, then make a small, recognizable change in the app,
   for example create a vendor named `Restore drill <date>`.
2. Wait a few minutes. Start a timer.
3. In the Neon Console, **Branches** > **New branch**. Parent: production.
   Name: `restore-drill`. Include **Past data** and choose a time just before
   step 1 (it must be within the history window, 6 hours on the free plan).
4. In the SQL Editor on `restore-drill`, confirm the drill vendor is absent
   and the rest of the data is present:

   ```sql
   SELECT count(*) FROM vendors WHERE name LIKE 'Restore drill%';  -- expect 0
   SELECT max(created_at) FROM audit_events;  -- just before your chosen time
   ```

5. Optionally start the app against the branch (as in the rehearsal) and open
   a report.
6. Stop the timer. Delete the branch and the drill vendor.

### Real recovery (overwrites production)

Use this only for an actual incident, such as a bad migration or deleted data.

1. **Stop writes:** scale the Northflank app service to 0 so nobody adds data
   that the restore would discard.
2. In the Neon Console, select the production branch, open
   **Backup & Restore**, and click **Restore from history**.
3. On **From history**, choose the timestamp just before the problem, click
   **Next**, review, and click **Restore**.
4. Neon keeps the pre-restore state as a branch named
   `<branch>_old_<timestamp>`, so the restore itself can be undone.
5. Check `SELECT version_num FROM alembic_version;`. If the restore went back
   past a migration, deploy the app version that matches that revision (or
   re-run the migration job).
6. Scale the app back up, sign in, and check a vendor, a report, and a chat.

**What a restore does not cover:** uploaded files in object storage are not
part of the database. After restoring to an earlier time, files uploaded after
that time remain in the bucket without a database row (harmless, but they take
space). The audit trail and usage records are restored with everything else.

### Drill record

| Item | Result |
| --- | --- |
| Date | |
| Restore point chosen | |
| Data lost (RPO) | e.g. "changes after 14:05" |
| Time to working copy (RTO) | |
| Problems found | |

## Free-plan limits that affect recovery

- **6-hour history window.** Point-in-time restore only reaches back 6 hours.
  A mistake noticed the next day cannot be restored from history.
- **One manual snapshot.** Before a risky migration or bulk change, take a
  snapshot from **Backup & Restore**; it is the only way back beyond 6 hours.
  Replace it before the next risky change.
- **Branches count toward storage and compute.** Delete rehearsal and drill
  branches when finished; inactive free branches are archived automatically.
- **Object storage is separate.** MinIO/S3 has its own retention; a database
  restore never brings back deleted files.
