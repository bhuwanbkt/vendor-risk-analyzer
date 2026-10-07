"""add hnsw index for document chunk embeddings

Revision ID: 11bc467f9cd8
Revises: 0469e45f43ae
Create Date: 2026-10-07 16:09:48.217345

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '11bc467f9cd8'
down_revision: Union[str, Sequence[str], None] = '0469e45f43ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX ix_document_chunks_embedding_hnsw
        ON document_chunks
        USING hnsw (
            embedding vector_cosine_ops
        )
        WHERE embedding IS NOT NULL
        """
    )



def downgrade() -> None:
    op.execute(
        """
        DROP INDEX
        ix_document_chunks_embedding_hnsw
        """
    )
