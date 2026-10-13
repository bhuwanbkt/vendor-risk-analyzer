"""Shared rate-limit state for all application instances.

Revision ID: d8a2c6e4f0b1
Revises: c5d9f1a3b7e2
"""

import sqlalchemy as sa
from alembic import op

revision = "d8a2c6e4f0b1"
down_revision = "c5d9f1a3b7e2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rate_limit_buckets",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("tat", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("rate_limit_buckets")
