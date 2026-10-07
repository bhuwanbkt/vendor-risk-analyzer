from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from vendor_risk_analyzer.embeddings.service import (
    EmbeddingService,
)


@dataclass
class RetrievalResult:
    chunk_id: str
    document_id: str
    sequence: int
    content: str
    metadata: dict[str, Any]
    cosine_distance: float
    similarity: float


def vector_to_pgvector_literal(
    embedding: list[float],
) -> str:
    return (
        "["
        + ",".join(
            format(value, ".10g")
            for value in embedding
        )
        + "]"
    )


class SemanticRetriever:
    def __init__(
        self,
        embedding_service: EmbeddingService,
    ) -> None:
        self.embedding_service = (
            embedding_service
        )

    async def search(
        self,
        *,
        db: AsyncSession,
        query: str,
        limit: int = 5,
        document_id: UUID | str | None = None,
    ) -> list[RetrievalResult]:

        clean_query = query.strip()

        if not clean_query:
            raise ValueError(
                "Query cannot be empty."
            )

        if limit < 1:
            raise ValueError(
                "limit must be at least 1."
            )

        query_embedding = (
            await self.embedding_service.embed_query(
                clean_query
            )
        )

        embedding_literal = (
            vector_to_pgvector_literal(
                query_embedding
            )
        )

        sql = """
            WITH query_vector AS (
                SELECT
                    CAST(
                        :query_embedding
                        AS vector(768)
                    ) AS embedding
            )

            SELECT
                dc.id::text
                    AS chunk_id,

                dc.document_id::text
                    AS document_id,

                dc.sequence,

                dc.content,

                dc.metadata,

                dc.embedding
                    <=>
                query_vector.embedding
                    AS cosine_distance,

                1 - (
                    dc.embedding
                        <=>
                    query_vector.embedding
                )
                    AS similarity

            FROM document_chunks dc

            JOIN documents d
                ON d.id = dc.document_id

            CROSS JOIN query_vector

            WHERE
                dc.embedding IS NOT NULL

                AND d.status = 'ready'

                AND dc.metadata->>'embedding_model'
                    = CAST(
                        :embedding_model
                        AS text
                    )

                AND (
                    dc.metadata
                        ->>
                    'embedding_dimensions'
                )::integer
                    = CAST(
                        :embedding_dimensions
                        AS integer
                    )
        """

        params: dict[str, Any] = {
            "query_embedding": (
                embedding_literal
            ),
            "embedding_model": (
                self.embedding_service.model
            ),
            "embedding_dimensions": (
                self.embedding_service.dimensions
            ),
            "limit": limit,
        }

        if document_id is not None:
            sql += """
                AND dc.document_id
                    = CAST(
                        :document_id
                        AS uuid
                    )
            """

            params["document_id"] = str(
                document_id
            )

        sql += """
            ORDER BY
                dc.embedding
                    <=>
                query_vector.embedding

            LIMIT CAST(
                :limit
                AS integer
            )
        """

        result = await db.execute(
            text(sql),
            params,
        )

        rows = (
            result
            .mappings()
            .all()
        )

        return [
            RetrievalResult(
                chunk_id=row["chunk_id"],
                document_id=(
                    row["document_id"]
                ),
                sequence=row["sequence"],
                content=row["content"],
                metadata=(
                    row["metadata"] or {}
                ),
                cosine_distance=float(
                    row["cosine_distance"]
                ),
                similarity=float(
                    row["similarity"]
                ),
            )
            for row in rows
        ]