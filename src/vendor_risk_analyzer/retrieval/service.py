from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from vendor_risk_analyzer.embeddings.service import (
    EmbeddingService,
)


# Route retrieval logs through Uvicorn so they
# appear in Northflank container logs.
logger = logging.getLogger(
    "uvicorn.error"
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
        logger.warning(
            "Retrieval rejected. "
            "Invalid %s.",
            field_name,
        )

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
        validation_started = (
            time.perf_counter()
        )

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

        duration = (
            time.perf_counter()
            - validation_started
        )

        if (
            result.scalar_one_or_none()
            is None
        ):
            logger.warning(
                "Retrieval rejected. "
                "Vendor not found. "
                "vendor_id=%s "
                "duration=%.3fs",
                vendor_id,
                duration,
            )

            raise VendorNotFoundError(
                "Vendor not found."
            )

        logger.info(
            "Retrieval vendor scope "
            "validated. "
            "vendor_id=%s "
            "duration=%.3fs",
            vendor_id,
            duration,
        )

    async def _validate_document(
        self,
        *,
        db: AsyncSession,
        vendor_id: str,
        document_id: str,
    ) -> None:
        validation_started = (
            time.perf_counter()
        )

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

        duration = (
            time.perf_counter()
            - validation_started
        )

        if (
            result.scalar_one_or_none()
            is None
        ):
            logger.warning(
                "Retrieval rejected. "
                "Document does not belong "
                "to vendor. "
                "vendor_id=%s "
                "document_id=%s "
                "duration=%.3fs",
                vendor_id,
                document_id,
                duration,
            )

            raise DocumentNotFoundError(
                "Document not found for vendor."
            )

        logger.info(
            "Retrieval document scope "
            "validated. "
            "vendor_id=%s "
            "document_id=%s "
            "duration=%.3fs",
            vendor_id,
            document_id,
            duration,
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
        retrieval_started = (
            time.perf_counter()
        )

        clean_query = query.strip()

        if not clean_query:
            logger.warning(
                "Retrieval rejected. "
                "Query was empty."
            )

            raise ValueError(
                "Query cannot be empty."
            )

        if limit < 1:
            logger.warning(
                "Retrieval rejected. "
                "Invalid limit=%s.",
                limit,
            )

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

        logger.info(
            "Retrieval started. "
            "vendor_id=%s "
            "document_id=%s "
            "limit=%s "
            "query_length=%s",
            normalized_vendor_id,
            (
                normalized_document_id
                or "ALL_READY_DOCUMENTS"
            ),
            limit,
            len(clean_query),
        )

        # ----------------------------------------------------
        # Validate retrieval scope
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Generate query embedding
        #
        # Do NOT log the raw query because it may
        # contain sensitive vendor information.
        # ----------------------------------------------------

        embedding_started = (
            time.perf_counter()
        )

        try:
            query_embedding = (
                await self.embedding_service
                .embed_query(
                    clean_query
                )
            )

        except Exception:
            embedding_duration = (
                time.perf_counter()
                - embedding_started
            )

            logger.exception(
                "Retrieval query embedding "
                "failed. "
                "vendor_id=%s "
                "document_id=%s "
                "query_length=%s "
                "duration=%.3fs",
                normalized_vendor_id,
                (
                    normalized_document_id
                    or "ALL_READY_DOCUMENTS"
                ),
                len(clean_query),
                embedding_duration,
            )

            raise

        embedding_duration = (
            time.perf_counter()
            - embedding_started
        )

        logger.info(
            "Retrieval query embedding "
            "generated. "
            "vendor_id=%s "
            "dimensions=%s "
            "model=%s "
            "duration=%.3fs",
            normalized_vendor_id,
            len(query_embedding),
            self.embedding_service.model,
            embedding_duration,
        )

        embedding_literal = (
            vector_to_pgvector_literal(
                query_embedding
            )
        )

        # ----------------------------------------------------
        # Vendor-scoped pgvector search
        # ----------------------------------------------------

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

        search_started = (
            time.perf_counter()
        )

        try:
            result = await db.execute(
                text(sql),
                params,
            )

        except Exception:
            search_duration = (
                time.perf_counter()
                - search_started
            )

            logger.exception(
                "pgvector retrieval failed. "
                "vendor_id=%s "
                "document_id=%s "
                "limit=%s "
                "duration=%.3fs",
                normalized_vendor_id,
                (
                    normalized_document_id
                    or "ALL_READY_DOCUMENTS"
                ),
                limit,
                search_duration,
            )

            raise

        search_duration = (
            time.perf_counter()
            - search_started
        )

        rows = (
            result
            .mappings()
            .all()
        )

        logger.info(
            "pgvector search completed. "
            "vendor_id=%s "
            "document_id=%s "
            "results=%s "
            "duration=%.3fs",
            normalized_vendor_id,
            (
                normalized_document_id
                or "ALL_READY_DOCUMENTS"
            ),
            len(rows),
            search_duration,
        )

        results = [
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

        total_duration = (
            time.perf_counter()
            - retrieval_started
        )

        if results:
            logger.info(
                "Retrieval completed. "
                "vendor_id=%s "
                "document_id=%s "
                "results=%s "
                "top_similarity=%.4f "
                "total_duration=%.3fs",
                normalized_vendor_id,
                (
                    normalized_document_id
                    or "ALL_READY_DOCUMENTS"
                ),
                len(results),
                results[0].similarity,
                total_duration,
            )

        else:
            logger.info(
                "Retrieval completed with "
                "no results. "
                "vendor_id=%s "
                "document_id=%s "
                "total_duration=%.3fs",
                normalized_vendor_id,
                (
                    normalized_document_id
                    or "ALL_READY_DOCUMENTS"
                ),
                total_duration,
            )

        return results