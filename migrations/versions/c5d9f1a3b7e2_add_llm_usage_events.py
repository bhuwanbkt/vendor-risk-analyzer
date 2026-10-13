"""Record Gemini token usage per company.

Revision ID: c5d9f1a3b7e2
Revises: b3e8d5c2a9f1
"""

import sqlalchemy as sa
from alembic import op

revision = "c5d9f1a3b7e2"
down_revision = "b3e8d5c2a9f1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "llm_usage_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("vendor_id", sa.Uuid(), nullable=True),
        sa.Column("feature", sa.String(32), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("reference_id", sa.String(64), nullable=True),
        sa.Column("actor_subject", sa.String(255), nullable=True),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("thought_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_llm_usage_tenant_created",
        "llm_usage_events",
        ["tenant_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_llm_usage_tenant_created", table_name="llm_usage_events")
    op.drop_table("llm_usage_events")
