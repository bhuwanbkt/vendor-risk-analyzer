from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from vendor_risk_analyzer.embeddings.service import (
    EmbeddingService,
)


class VendorNotFoundError(ValueError):
    """Raised when the requested vendor does not exist."""


class DocumentNotFoundError(ValueError):
    """Raised when a document does not belong to the vendor."""


@dataclass
class RetrievalResult:
    chunk_id: str
    document_id: str
    sequence: int
    content: str
    metadata: dict[str, Any]
    cosine_distance: float
    similarity: float


def normalize_uuid(
    value: UUID | str,
    *,
    field_name: str,
) -> str:
    try:
        return str(
            UUID(str(value))
        )
    except ValueError as exc:
        raise ValueError(
            f"Invalid {field_name}."
        ) from exc


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

    async def _validate_vendor(
        self,
        *,
        db: AsyncSession,
        vendor_id: str,
    ) -> None:
        result = await db.execute(
            text(
                """
                SELECT 1
                FROM vendors
                WHERE id = CAST(
                    :vendor_id AS uuid
                )
                LIMIT 1
                """
            ),
            {
                "vendor_id": vendor_id,
            },
        )

        if (
            result.scalar_one_or_none()
            is None
        ):
            raise VendorNotFoundError(
                "Vendor not found."
            )

    async def _validate_document(
        self,
        *,
        db: AsyncSession,
        vendor_id: str,
        document_id: str,
    ) -> None:
        result = await db.execute(
            text(
                """
                SELECT 1
                FROM documents
                WHERE
                    id = CAST(
                        :document_id AS uuid
                    )
                    AND vendor_id = CAST(
                        :vendor_id AS uuid
                    )
                LIMIT 1
                """
            ),
            {
                "vendor_id": vendor_id,
                "document_id": document_id,
            },
        )

        if (
            result.scalar_one_or_none()
            is None
        ):
            raise DocumentNotFoundError(
                "Document not found for vendor."
            )

    async def search(
        self,
        *,
        db: AsyncSession,
        query: str,
        vendor_id: UUID | str,
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

        normalized_vendor_id = (
            normalize_uuid(
                vendor_id,
                field_name="vendor_id",
            )
        )

        normalized_document_id = None

        if document_id is not None:
            normalized_document_id = (
                normalize_uuid(
                    document_id,
                    field_name="document_id",
                )
            )

        await self._validate_vendor(
            db=db,
            vendor_id=normalized_vendor_id,
        )

        if (
            normalized_document_id
            is not None
        ):
            await self._validate_document(
                db=db,
                vendor_id=normalized_vendor_id,
                document_id=(
                    normalized_document_id
                ),
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

                AND d.vendor_id = CAST(
                    :vendor_id AS uuid
                )

                AND dc.metadata
                    ->>'embedding_model'
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
            "vendor_id": (
                normalized_vendor_id
            ),
            "embedding_model": (
                self.embedding_service.model
            ),
            "embedding_dimensions": (
                self.embedding_service.dimensions
            ),
            "limit": limit,
        }

        if (
            normalized_document_id
            is not None
        ):
            sql += """
                AND dc.document_id = CAST(
                    :document_id AS uuid
                )
            """

            params["document_id"] = (
                normalized_document_id
            )

        sql += """
            ORDER BY
                dc.embedding
                    <=>
                query_vector.embedding

            LIMIT CAST(
                :limit AS integer
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