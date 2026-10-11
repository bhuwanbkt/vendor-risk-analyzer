"""Add company membership and vendor assignments, preserving existing records.

Revision ID: 2e1d4a7b9c60
Revises: 11bc467f9cd8
"""

import sqlalchemy as sa
from alembic import op

revision = "2e1d4a7b9c60"
down_revision = "11bc467f9cd8"
branch_labels = None
depends_on = None
LEGACY_ID = "00000000-0000-0000-0000-000000000001"


def identifiers():
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "tenants",
        *identifiers(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("zitadel_org_id", sa.String(255), unique=True),
        sa.Column("default_access", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "default_access IN ('all', 'assigned')", name="ck_tenant_default_access"
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO tenants (id, name, zitadel_org_id, default_access, created_at, updated_at) "
            f"VALUES ('{LEGACY_ID}', 'Existing workspace', NULL, 'all', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )
    # Server default permits the previous application release to keep creating
    # vendors between running this migration and deploying the new application.
    op.add_column(
        "vendors",
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.text(f"'{LEGACY_ID}'"),
        ),
    )
    op.create_foreign_key(
        "fk_vendor_tenant",
        "vendors",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_vendors_tenant_id", "vendors", ["tenant_id"])
    op.drop_index("ix_vendors_name", table_name="vendors")
    op.create_index("ix_vendors_name", "vendors", ["name"])
    op.create_unique_constraint(
        "uq_vendor_tenant_name", "vendors", ["tenant_id", "name"]
    )
    op.create_unique_constraint("uq_vendor_tenant_id", "vendors", ["tenant_id", "id"])
    op.create_table(
        "tenant_memberships",
        *identifiers(),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("email", sa.String(320)),
        sa.Column("access_mode", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "subject", name="uq_membership_tenant_subject"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_membership_tenant_id"),
        sa.CheckConstraint(
            "access_mode IN ('all', 'assigned')", name="ck_membership_access_mode"
        ),
    )
    op.create_table(
        "vendor_assignments",
        *identifiers(),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("vendor_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "member_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="CASCADE",
            name="fk_assignment_membership",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            ondelete="CASCADE",
            name="fk_assignment_vendor",
        ),
        sa.UniqueConstraint(
            "member_id", "vendor_id", name="uq_assignment_member_vendor"
        ),
    )
    op.create_index(
        "ix_assignment_tenant_member", "vendor_assignments", ["tenant_id", "member_id"]
    )


def downgrade():
    # Do not flatten separate companies back into a globally shared workspace.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM tenants")).scalar() > 1:
        raise RuntimeError(
            "Cannot downgrade while multiple companies exist; restore a pre-migration backup instead"
        )
    op.drop_table("vendor_assignments")
    op.drop_table("tenant_memberships")
    op.drop_constraint("uq_vendor_tenant_name", "vendors", type_="unique")
    op.drop_constraint("uq_vendor_tenant_id", "vendors", type_="unique")
    op.drop_index("ix_vendors_name", table_name="vendors")
    op.create_index("ix_vendors_name", "vendors", ["name"], unique=True)
    op.drop_constraint("fk_vendor_tenant", "vendors", type_="foreignkey")
    op.drop_index("ix_vendors_tenant_id", table_name="vendors")
    op.drop_column("vendors", "tenant_id")
    op.drop_table("tenants")
