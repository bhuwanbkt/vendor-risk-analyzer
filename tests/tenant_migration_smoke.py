"""CI-only migration check against the disposable Compose PostgreSQL database.

Mounted into the migrate container by CI; never run against a hosted database.
"""

import argparse
import asyncio
import os
from uuid import UUID

import asyncpg
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from vendor_risk_analyzer.db import models  # noqa: F401
from vendor_risk_analyzer.db.base import Base
from vendor_risk_analyzer.db.url import get_database_url

LEGACY_ID = UUID(int=1)
VENDOR_ID, DOCUMENT_ID, ASSESSMENT_ID, FINDING_ID = [
    UUID(int=i) for i in range(9001, 9005)
]


async def seed(connection):
    assert (
        await connection.fetchval("SELECT version_num FROM alembic_version")
        == "11bc467f9cd8"
    )
    async with connection.transaction():
        await connection.execute(
            """INSERT INTO vendors (id, name, status, metadata, created_at, updated_at)
            VALUES ($1, 'Migration retained vendor', 'active', '{}', now(), now())""",
            VENDOR_ID,
        )
        await connection.execute(
            """INSERT INTO documents (id, vendor_id, filename, object_key, file_type,
            sha256, status, metadata, created_at, updated_at)
            VALUES ($1, $2, 'retained.txt', 'migration/retained.txt', 'txt', $3, 'uploaded', '{}', now(), now())""",
            DOCUMENT_ID,
            VENDOR_ID,
            "e" * 64,
        )
        await connection.execute(
            """INSERT INTO assessments (id, vendor_id, status, assessment_type,
            metadata, created_at, updated_at) VALUES ($1, $2, 'completed', 'vendor_risk',
            '{"analysis_summary":"Retained summary"}', now(), now())""",
            ASSESSMENT_ID,
            VENDOR_ID,
        )
        await connection.execute(
            """INSERT INTO findings (id, assessment_id, document_id, category,
            severity, title, description, status, metadata, created_at, updated_at)
            VALUES ($1, $2, $3, 'security', 'medium', 'Retained finding', 'Retained evidence', 'open', '{}', now(), now())""",
            FINDING_ID,
            ASSESSMENT_ID,
            DOCUMENT_ID,
        )
    print(
        "Seeded existing vendor, document, assessment, and finding at the previous revision"
    )


def migration_head() -> str:
    """The newest migration, so this check survives future migrations."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


async def verify(connection):
    assert (
        await connection.fetchval("SELECT version_num FROM alembic_version")
        == migration_head()
    )
    assert (
        await connection.fetchval(
            "SELECT tenant_id FROM vendors WHERE id=$1", VENDOR_ID
        )
        == LEGACY_ID
    )
    assert (
        await connection.fetchval(
            "SELECT zitadel_org_id FROM tenants WHERE id=$1", LEGACY_ID
        )
        is None
    )
    assert (
        await connection.fetchval(
            "SELECT default_access FROM tenants WHERE id=$1", LEGACY_ID
        )
        == "all"
    )
    assert (
        await connection.fetchval(
            "SELECT vendor_id FROM documents WHERE id=$1", DOCUMENT_ID
        )
        == VENDOR_ID
    )
    assert "Retained summary" in await connection.fetchval(
        "SELECT metadata::text FROM assessments WHERE id=$1", ASSESSMENT_ID
    )
    assert (
        await connection.fetchval(
            "SELECT document_id FROM findings WHERE id=$1", FINDING_ID
        )
        == DOCUMENT_ID
    )
    transaction = connection.transaction()
    await transaction.start()
    try:
        other, member = UUID(int=9100), UUID(int=9101)
        await connection.execute(
            """INSERT INTO tenants (id, name, zitadel_org_id, default_access, created_at, updated_at)
            VALUES ($1, 'Other migration company', 'migration-other-org', 'assigned', now(), now())""",
            other,
        )
        # The same vendor name is permitted in a different company.
        await connection.execute(
            """INSERT INTO vendors (id, tenant_id, name, status, metadata, created_at, updated_at)
            VALUES ($1, $2, 'Migration retained vendor', 'active', '{}', now(), now())""",
            UUID(int=9102),
            other,
        )
        await connection.execute(
            """INSERT INTO tenant_memberships (id, tenant_id, subject, access_mode, enabled, created_at, updated_at)
            VALUES ($1, $2, 'migration-member', 'assigned', true, now(), now())""",
            member,
            LEGACY_ID,
        )
        for assignment_tenant, vendor in [
            (other, VENDOR_ID),
            (LEGACY_ID, UUID(int=9102)),
        ]:
            try:
                async with connection.transaction():
                    await connection.execute(
                        """INSERT INTO vendor_assignments (id, tenant_id, member_id, vendor_id, created_at, updated_at)
                        VALUES ($1, $2, $3, $4, now(), now())""",
                        UUID(int=9200),
                        assignment_tenant,
                        member,
                        vendor,
                    )
            except asyncpg.ForeignKeyViolationError:
                pass
            else:
                raise AssertionError("Database accepted a cross-company assignment")
        try:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO vendors (id, tenant_id, name, status, metadata, created_at, updated_at)
                    VALUES ($1, $2, 'Migration retained vendor', 'active', '{}', now(), now())""",
                    UUID(int=9300),
                    LEGACY_ID,
                )
        except asyncpg.UniqueViolationError:
            pass
        else:
            raise AssertionError("Database accepted a duplicate company vendor name")
        # The previous app version can still insert before the new app deploys.
        await connection.execute(
            """INSERT INTO vendors (id, name, status, metadata, created_at, updated_at)
            VALUES ($1, 'Previous application release', 'active', '{}', now(), now())""",
            UUID(int=9400),
        )
        assert (
            await connection.fetchval(
                "SELECT tenant_id FROM vendors WHERE id=$1", UUID(int=9400)
            )
            == LEGACY_ID
        )
    finally:
        await transaction.rollback()
    print(
        "PostgreSQL migration preserved existing records, locked legacy access, scoped uniqueness, and rejected cross-company assignments"
    )


async def main(args):
    url = make_url(os.environ["DATABASE_URL"])
    if (
        os.environ.get("APP_ENV") != "development"
        or url.host not in {"db", "localhost", "127.0.0.1"}
        or url.database != "vendor_risk"
    ):
        raise ValueError(
            "This check only accepts the disposable development Compose database"
        )
    connection = await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
    )
    try:
        await (seed(connection) if args.seed else verify(connection))
    finally:
        await connection.close()
    if not args.seed:
        engine = create_async_engine(get_database_url())
        try:
            async with engine.connect() as conn:

                def compare(sync):
                    ctx = MigrationContext.configure(sync, opts={"compare_type": True})
                    # The existing HNSW index is migration-owned, not ORM-owned.
                    differences = [
                        diff
                        for diff in compare_metadata(ctx, Base.metadata)
                        if not (
                            diff[0] == "remove_index"
                            and diff[1].name == "ix_document_chunks_embedding_hnsw"
                        )
                    ]
                    assert not differences, differences

                await conn.run_sync(compare)
        finally:
            await engine.dispose()
        print("Migrated PostgreSQL schema matches application models")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    asyncio.run(main(parser.parse_args()))
