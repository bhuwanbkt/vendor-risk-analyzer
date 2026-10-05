"""set embedding vector dimension

Revision ID: 0469e45f43ae
Revises: bbbf5239e042
Create Date: 2026-10-05 16:28:07.626499

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0469e45f43ae'
down_revision: Union[str, Sequence[str], None] = 'bbbf5239e042'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE document_chunks
        ALTER COLUMN embedding
        TYPE vector(768)
        USING embedding::vector(768)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE document_chunks
        ALTER COLUMN embedding
        TYPE vector
        USING embedding::vector
        """
    )