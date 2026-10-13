"""CI-only row-level security check against the disposable Compose database.

Runs after all migrations. Creates a throwaway login role that is subject to
RLS (like the production app role), seeds two companies with a row in every
protected table, and verifies that:

- a session with no company sees nothing,
- a session bound to one company sees only that company's rows,
- writes into another company are rejected,
- background (system) access sees both companies,
- ``rls_status`` reports enforcement for the restricted role.

Everything it creates is removed at the end. Never run against a hosted database.
"""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

import asyncpg
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vendor_risk_analyzer.db import tenant_context
from vendor_risk_analyzer.db.url import get_database_url

ROLE = "rls_smoke_app"
PASSWORD = "rls-smoke-only"
COMPANIES = {
    "one": {
        "tenant": UUID(int=7101), "member": UUID(int=7111), "vendor": UUID(int=7121),
        "document": UUID(int=7131), "element": UUID(int=7141), "chunk": UUID(int=7151),
        "assessment": UUID(int=7161), "finding": UUID(int=7171),
        "conversation": UUID(int=7181), "message": UUID(int=7191),
        "assignment": UUID(int=7201), "audit": UUID(int=7211), "usage": UUID(int=7221),
    },
    "two": {
        "tenant": UUID(int=7102), "member": UUID(int=7112), "vendor": UUID(int=7122),
        "document": UUID(int=7132), "element": UUID(int=7142), "chunk": UUID(int=7152),
        "assessment": UUID(int=7162), "finding": UUID(int=7172),
        "conversation": UUID(int=7182), "message": UUID(int=7192),
        "assignment": UUID(int=7202), "audit": UUID(int=7212), "usage": UUID(int=7222),
    },
}
# Protected table -> the seeded row's key in COMPANIES.
TABLES = {
    "vendors": "vendor",
    "tenant_memberships": "member",
    "vendor_assignments": "assignment",
    "documents": "document",
    "document_elements": "element",
    "document_chunks": "chunk",
    "assessments": "assessment",
    "findings": "finding",
    "chat_conversations": "conversation",
    "chat_messages": "message",
    "audit_events": "audit",
    "llm_usage_events": "usage",
}


async def seed(connection) -> None:
    async with connection.transaction():
        for name, ids in COMPANIES.items():
            await connection.execute(
                """INSERT INTO tenants (id, name, zitadel_org_id, default_access,
                created_at, updated_at) VALUES ($1, $2, $3, 'all', now(), now())""",
                ids["tenant"], f"RLS smoke {name}", f"rls-smoke-{name}",
            )
            await connection.execute(
                """INSERT INTO tenant_memberships (id, tenant_id, subject, access_mode,
                enabled, created_at, updated_at)
                VALUES ($1, $2, $3, 'all', true, now(), now())""",
                ids["member"], ids["tenant"], f"rls-smoke-user-{name}",
            )
            await connection.execute(
                """INSERT INTO vendors (id, tenant_id, name, status, metadata,
                created_at, updated_at) VALUES ($1, $2, $3, 'active', '{}', now(), now())""",
                ids["vendor"], ids["tenant"], f"RLS smoke vendor {name}",
            )
            await connection.execute(
                """INSERT INTO vendor_assignments (id, tenant_id, member_id, vendor_id,
                created_at, updated_at) VALUES ($1, $2, $3, $4, now(), now())""",
                ids["assignment"], ids["tenant"], ids["member"], ids["vendor"],
            )
            await connection.execute(
                """INSERT INTO documents (id, vendor_id, filename, object_key, file_type,
                sha256, status, metadata, created_at, updated_at)
                VALUES ($1, $2, 'rls.txt', $3, 'txt', $4, 'ready', '{}', now(), now())""",
                ids["document"], ids["vendor"], f"rls/{name}.txt", "f" * 64,
            )
            await connection.execute(
                """INSERT INTO document_elements (id, document_id, element_type, sequence,
                content, heading_path, metadata, created_at, updated_at)
                VALUES ($1, $2, 'paragraph', 0, 'RLS smoke', '[]', '{}', now(), now())""",
                ids["element"], ids["document"],
            )
            await connection.execute(
                """INSERT INTO document_chunks (id, document_id, sequence, content,
                metadata, created_at, updated_at)
                VALUES ($1, $2, 0, 'RLS smoke chunk', '{}', now(), now())""",
                ids["chunk"], ids["document"],
            )
            await connection.execute(
                """INSERT INTO assessments (id, vendor_id, status, assessment_type,
                metadata, created_at, updated_at)
                VALUES ($1, $2, 'completed', 'vendor_risk', '{}', now(), now())""",
                ids["assessment"], ids["vendor"],
            )
            await connection.execute(
                """INSERT INTO findings (id, assessment_id, category, severity, title,
                description, status, metadata, created_at, updated_at)
                VALUES ($1, $2, 'access_control', 'high', 'RLS smoke', 'RLS smoke',
                'open', '{}', now(), now())""",
                ids["finding"], ids["assessment"],
            )
            await connection.execute(
                """INSERT INTO chat_conversations (id, tenant_id, vendor_id,
                member_subject, title, created_at, updated_at)
                VALUES ($1, $2, $3, $4, 'RLS smoke', now(), now())""",
                ids["conversation"], ids["tenant"], ids["vendor"], f"rls-smoke-user-{name}",
            )
            await connection.execute(
                """INSERT INTO chat_messages (id, conversation_id, role, content, sources,
                created_at, updated_at) VALUES ($1, $2, 'user', 'RLS smoke', '[]', now(), now())""",
                ids["message"], ids["conversation"],
            )
            await connection.execute(
                """INSERT INTO audit_events (id, tenant_id, action, target_type, details,
                created_at, updated_at) VALUES ($1, $2, 'vendor.created', 'vendor', '{}', now(), now())""",
                ids["audit"], ids["tenant"],
            )
            await connection.execute(
                """INSERT INTO llm_usage_events (id, tenant_id, feature, model, requests,
                input_tokens, output_tokens, thought_tokens, total_tokens, created_at, updated_at)
                VALUES ($1, $2, 'chat', 'smoke', 1, 1, 1, 0, 2, now(), now())""",
                ids["usage"], ids["tenant"],
            )


async def cleanup(connection) -> None:
    # Children first; not every foreign key cascades.
    order = [
        "chat_messages", "chat_conversations", "findings", "assessments",
        "document_chunks", "document_elements", "documents", "vendor_assignments",
        "audit_events", "llm_usage_events", "vendors", "tenant_memberships",
    ]
    async with connection.transaction():
        for table in order:
            ids = [company[TABLES[table]] for company in COMPANIES.values()]
            await connection.execute(f"DELETE FROM {table} WHERE id = ANY($1::uuid[])", ids)
        await connection.execute(
            "DELETE FROM tenants WHERE id = ANY($1::uuid[])",
            [company["tenant"] for company in COMPANIES.values()],
        )
    await connection.execute(f"DROP OWNED BY {ROLE}")
    await connection.execute(f"DROP ROLE IF EXISTS {ROLE}")


async def counts(db) -> dict[str, int]:
    """Visible seeded rows per protected table (other data is ignored)."""
    result = {}
    for table, key in TABLES.items():
        ids = [str(company[key]) for company in COMPANIES.values()]
        result[table] = await db.scalar(
            text(f"SELECT count(*) FROM {table} WHERE id::text = ANY(:ids)"),
            {"ids": ids},
        )
    return result


async def main() -> None:
    url = make_url(os.environ["DATABASE_URL"])
    if (
        os.environ.get("APP_ENV") != "development"
        or url.host not in {"db", "localhost", "127.0.0.1"}
        or url.database != "vendor_risk"
    ):
        raise ValueError("This check only accepts the disposable development Compose database")

    owner = await asyncpg.connect(
        host=url.host, port=url.port or 5432, user=url.username,
        password=url.password, database=url.database,
    )
    await owner.execute(f"DROP ROLE IF EXISTS {ROLE}")
    await owner.execute(f"CREATE ROLE {ROLE} LOGIN PASSWORD '{PASSWORD}'")
    await owner.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    await owner.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROLE}"
    )
    await seed(owner)
    print("Seeded two companies with a row in every protected table")

    app_url = make_url(get_database_url()).set(username=ROLE, password=PASSWORD)
    engine = create_async_engine(app_url)
    system_engine = create_async_engine(app_url)
    tenant_context.enable_system_access(system_engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            status = await tenant_context.rls_status(db)
            assert status["enforced"], status
            unbound = await counts(db)
            assert all(value == 0 for value in unbound.values()), unbound

        for name, ids in COMPANIES.items():
            async with sessions() as db:
                await tenant_context.bind_tenant(db, ids["tenant"])
                visible = await counts(db)
                # Exactly this company's single row in every protected table.
                assert all(value == 1 for value in visible.values()), (name, visible)
                own_vendor = await db.scalar(
                    text("SELECT count(*) FROM vendors WHERE id = :id"),
                    {"id": ids["vendor"]},
                )
                assert own_vendor == 1, name

        async with sessions() as db:
            await tenant_context.bind_tenant(db, COMPANIES["one"]["tenant"])
            try:
                await db.execute(
                    text(
                        "INSERT INTO vendors (id, tenant_id, name, status, metadata, "
                        "created_at, updated_at) VALUES (gen_random_uuid(), :tenant, "
                        "'cross-company', 'active', '{}', now(), now())"
                    ),
                    {"tenant": COMPANIES["two"]["tenant"]},
                )
            except Exception as exc:
                assert "row-level security" in str(exc), exc
            else:
                raise AssertionError("Insert into another company was allowed")

        async with system_engine.connect() as connection:
            everything = await counts(connection)
            assert all(value == 2 for value in everything.values()), everything
    finally:
        await engine.dispose()
        await system_engine.dispose()
        await cleanup(owner)
        await owner.close()
    print(
        "Row-level security isolates every company table for a restricted role; "
        "system access sees all companies"
    )


if __name__ == "__main__":
    asyncio.run(main())
