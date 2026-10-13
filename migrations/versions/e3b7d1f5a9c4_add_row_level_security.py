"""Row-level security: each company's rows are visible only to that company.

The application tells PostgreSQL which company a transaction is for with
``set_config('app.tenant_id', <uuid>, true)``. Background workers, which
process queued work for every company, set ``app.rls_bypass`` instead.
Both are transaction-local, so they are safe with connection poolers.

Policies apply only to roles that are subject to RLS: not superusers, not
roles with BYPASSRLS, and not the tables' owner (FORCE is not used, so the
migration and operations jobs keep full access). To enforce them, run the
app with its own login role; see docs/row-level-security.md.

Revision ID: e3b7d1f5a9c4
Revises: d8a2c6e4f0b1
"""

from alembic import op

revision = "e3b7d1f5a9c4"
down_revision = "d8a2c6e4f0b1"
branch_labels = None
depends_on = None


# One statement per op.execute: asyncpg prepares each statement, and
# PostgreSQL rejects several commands in one prepared statement.
FUNCTIONS = [
    """
    CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE AS $$
        SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION app_rls_bypass() RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE AS $$
        SELECT coalesce(current_setting('app.rls_bypass', true), '') = 'on'
    $$
    """,
]

# Table -> condition that a row belongs to the current company.
OWNERSHIP = {
    "vendors": "tenant_id = app_current_tenant()",
    "tenant_memberships": "tenant_id = app_current_tenant()",
    "vendor_assignments": "tenant_id = app_current_tenant()",
    "chat_conversations": "tenant_id = app_current_tenant()",
    "audit_events": "tenant_id = app_current_tenant()",
    "llm_usage_events": "tenant_id = app_current_tenant()",
    "documents": (
        "EXISTS (SELECT 1 FROM vendors v WHERE v.id = documents.vendor_id "
        "AND v.tenant_id = app_current_tenant())"
    ),
    "assessments": (
        "EXISTS (SELECT 1 FROM vendors v WHERE v.id = assessments.vendor_id "
        "AND v.tenant_id = app_current_tenant())"
    ),
    "document_elements": (
        "EXISTS (SELECT 1 FROM documents d JOIN vendors v ON v.id = d.vendor_id "
        "WHERE d.id = document_elements.document_id "
        "AND v.tenant_id = app_current_tenant())"
    ),
    "document_chunks": (
        "EXISTS (SELECT 1 FROM documents d JOIN vendors v ON v.id = d.vendor_id "
        "WHERE d.id = document_chunks.document_id "
        "AND v.tenant_id = app_current_tenant())"
    ),
    "findings": (
        "EXISTS (SELECT 1 FROM assessments a JOIN vendors v ON v.id = a.vendor_id "
        "WHERE a.id = findings.assessment_id "
        "AND v.tenant_id = app_current_tenant())"
    ),
    "chat_messages": (
        "EXISTS (SELECT 1 FROM chat_conversations c "
        "WHERE c.id = chat_messages.conversation_id "
        "AND c.tenant_id = app_current_tenant())"
    ),
}

POLICY = "company_isolation"


def policy_statements(table: str, condition: str) -> list[str]:
    rule = f"app_rls_bypass() OR ({condition})"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {POLICY} ON {table}",
        f"CREATE POLICY {POLICY} ON {table} FOR ALL "
        f"USING ({rule}) WITH CHECK ({rule})",
    ]


def upgrade():
    for statement in FUNCTIONS:
        op.execute(statement)
    for table, condition in OWNERSHIP.items():
        for statement in policy_statements(table, condition):
            op.execute(statement)


def downgrade():
    for table in OWNERSHIP:
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS app_rls_bypass()")
    op.execute("DROP FUNCTION IF EXISTS app_current_tenant()")
