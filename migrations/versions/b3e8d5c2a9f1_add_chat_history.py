"""Add saved chat conversations.

Revision ID: b3e8d5c2a9f1
Revises: a7c4e2f91b3d
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b3e8d5c2a9f1"
down_revision = "a7c4e2f91b3d"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "chat_conversations",
        *timestamps(),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vendor_id",
            sa.Uuid(),
            sa.ForeignKey("vendors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("member_subject", sa.String(255), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
    )
    op.create_index(
        "ix_chat_conversations_vendor_id", "chat_conversations", ["vendor_id"]
    )
    op.create_index(
        "ix_chat_conversations_owner",
        "chat_conversations",
        ["tenant_id", "member_subject", "vendor_id", "updated_at"],
    )

    op.create_table(
        "chat_messages",
        *timestamps(),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "sources",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("model", sa.String(100), nullable=True),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_message_role"),
    )
    op.create_index(
        "ix_chat_messages_conversation_id", "chat_messages", ["conversation_id"]
    )


def downgrade():
    op.drop_index("ix_chat_messages_conversation_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_conversations_owner", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_vendor_id", table_name="chat_conversations")
    op.drop_table("chat_conversations")
