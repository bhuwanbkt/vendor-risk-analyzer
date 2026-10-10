"""Embedding storage and document lifecycle shared by runtime and ops."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text


# ============================================================
# Metadata helpers
# ============================================================


def normalize_metadata(
    value: Any,
) -> dict[str, Any]:
    """
    Return metadata as a normal Python dictionary.
    """

    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(
                value
            )

            if isinstance(
                parsed,
                dict,
            ):
                return parsed

        except json.JSONDecodeError:
            pass

    return {}


def build_embedding_title(
    metadata: dict[str, Any],
) -> str | None:
    """
    Build structural context for the embedding.

    Priority:
        heading_path
        table_title
        caption
        sheet_name
    """

    heading_path = metadata.get(
        "heading_path"
    )

    if isinstance(
        heading_path,
        list,
    ):
        clean_headings = [
            str(value).strip()
            for value in heading_path
            if str(value).strip()
        ]

        if clean_headings:
            return " > ".join(
                clean_headings
            )

    for key in (
        "table_title",
        "caption",
        "sheet_name",
    ):
        value = metadata.get(
            key
        )

        if isinstance(
            value,
            str,
        ):
            value = value.strip()

            if value:
                return value

    return None


def vector_to_pgvector_literal(
    embedding: list[float],
) -> str:
    """
    Convert a Python list into pgvector text format.

    Example:
        [0.1,-0.2,0.3]
    """

    return (
        "["
        + ",".join(
            format(
                value,
                ".10g",
            )
            for value in embedding
        )
        + "]"
    )


# ============================================================
# Chunk queries
# ============================================================


async def fetch_chunks(
    engine,
    *,
    limit: int,
    document_id: str | None,
) -> list[dict[str, Any]]:
    """
    Fetch chunks that still need embeddings.

    Existing embedded chunks are automatically skipped.
    """

    sql = """
        SELECT
            id::text AS id,
            document_id::text AS document_id,
            sequence,
            content,
            metadata
        FROM document_chunks
        WHERE embedding IS NULL
    """

    params: dict[str, Any] = {
        "limit": limit,
    }

    if document_id:
        sql += """
            AND document_id =
                CAST(
                    :document_id
                    AS uuid
                )
        """

        params["document_id"] = (
            document_id
        )

    sql += """
        ORDER BY
            document_id,
            sequence
        LIMIT :limit
    """

    async with (
        engine.connect()
        as connection
    ):
        result = (
            await connection.execute(
                text(sql),
                params,
            )
        )

        return [
            dict(row)
            for row
            in result.mappings().all()
        ]


async def save_embedding(
    engine,
    *,
    chunk_id: str,
    embedding: list[float],
    model: str,
    dimensions: int,
) -> None:
    """
    Save exactly one embedding.

    Every chunk is committed independently.

    This means that if a later chunk fails,
    previously saved vectors remain safely stored.
    """

    embedding_literal = (
        vector_to_pgvector_literal(
            embedding
        )
    )

    sql = text(
        """
        UPDATE document_chunks
        SET
            embedding =
                CAST(
                    :embedding
                    AS vector(768)
                ),

            metadata =
                COALESCE(
                    metadata,
                    '{}'::jsonb
                )
                ||
                jsonb_build_object(
                    'embedding_provider',
                    'google',

                    'embedding_model',
                    CAST(
                        :embedding_model
                        AS text
                    ),

                    'embedding_dimensions',
                    CAST(
                        :embedding_dimensions
                        AS integer
                    )
                ),

            updated_at = NOW()

        WHERE
            id =
                CAST(
                    :chunk_id
                    AS uuid
                )

            AND embedding IS NULL
        """
    )

    async with (
        engine.begin()
        as connection
    ):
        result = (
            await connection.execute(
                sql,
                {
                    "embedding": (
                        embedding_literal
                    ),
                    "chunk_id": (
                        chunk_id
                    ),
                    "embedding_model": (
                        model
                    ),
                    "embedding_dimensions": (
                        dimensions
                    ),
                },
            )
        )

        if result.rowcount != 1:
            raise RuntimeError(
                "Expected to update exactly "
                "one chunk, but updated "
                f"{result.rowcount}."
            )


async def verify_embedding(
    engine,
    *,
    chunk_id: str,
) -> dict[str, Any]:
    """
    Verify that PostgreSQL contains the vector
    and that the vector has the correct dimension.
    """

    sql = text(
        """
        SELECT
            id::text AS id,

            embedding IS NOT NULL
                AS has_embedding,

            vector_dims(embedding)
                AS dimensions,

            metadata

        FROM document_chunks

        WHERE
            id =
                CAST(
                    :chunk_id
                    AS uuid
                )
        """
    )

    async with (
        engine.connect()
        as connection
    ):
        result = (
            await connection.execute(
                sql,
                {
                    "chunk_id": (
                        chunk_id
                    ),
                },
            )
        )

        row = (
            result
            .mappings()
            .one()
        )

        return dict(row)


# ============================================================
# Document lifecycle
# ============================================================


async def update_document_status(
    engine,
    *,
    document_id: str,
    status: str,
) -> None:
    """
    Update the lifecycle status for one document.

    Supported lifecycle values used by this worker:

        embedding_pending
        embedding
        ready
        embedding_failed
    """

    sql = text(
        """
        UPDATE documents
        SET
            status = CAST(
                :status
                AS varchar
            ),
            updated_at = NOW()

        WHERE
            id =
                CAST(
                    :document_id
                    AS uuid
                )
        """
    )

    async with (
        engine.begin()
        as connection
    ):
        result = (
            await connection.execute(
                sql,
                {
                    "document_id": (
                        document_id
                    ),
                    "status": status,
                },
            )
        )

        if result.rowcount != 1:
            raise RuntimeError(
                "Expected to update exactly "
                "one document status, "
                f"but updated {result.rowcount}."
            )


async def get_document_embedding_state(
    engine,
    *,
    document_id: str,
) -> dict[str, int]:
    """
    Return embedding progress for one document.

    Example:

        total_chunks = 20
        embedded_chunks = 18
        missing_embeddings = 2
    """

    sql = text(
        """
        SELECT
            COUNT(*) AS total_chunks,

            COUNT(embedding)
                AS embedded_chunks,

            COUNT(*)
            - COUNT(embedding)
                AS missing_embeddings

        FROM document_chunks

        WHERE
            document_id =
                CAST(
                    :document_id
                    AS uuid
                )
        """
    )

    async with (
        engine.connect()
        as connection
    ):
        result = (
            await connection.execute(
                sql,
                {
                    "document_id": (
                        document_id
                    ),
                },
            )
        )

        row = (
            result
            .mappings()
            .one()
        )

    return {
        "total_chunks": int(
            row["total_chunks"]
        ),
        "embedded_chunks": int(
            row["embedded_chunks"]
        ),
        "missing_embeddings": int(
            row["missing_embeddings"]
        ),
    }


async def finalize_document(
    engine,
    *,
    document_id: str,
) -> str:
    """
    Decide the correct lifecycle status after
    successful processing.

    ready
        Every chunk has an embedding.

    embedding_pending
        Some chunks still require embeddings.

    A document with zero chunks is never marked ready.
    """

    state = (
        await get_document_embedding_state(
            engine,
            document_id=document_id,
        )
    )

    total_chunks = state[
        "total_chunks"
    ]

    missing_embeddings = state[
        "missing_embeddings"
    ]

    if (
        total_chunks > 0
        and missing_embeddings == 0
    ):
        status = "ready"

    else:
        status = (
            "embedding_pending"
        )

    await update_document_status(
        engine,
        document_id=document_id,
        status=status,
    )

    return status


async def mark_document_failed(
    engine,
    *,
    document_id: str,
) -> None:
    """
    Best-effort transition to embedding_failed.

    If the database itself is unavailable,
    we print the status-update failure without
    hiding the original embedding error.
    """

    try:
        await update_document_status(
            engine,
            document_id=document_id,
            status="embedding_failed",
        )

    except Exception as exc:
        print(
            "WARNING: Could not mark "
            "document as embedding_failed."
        )

        print(
            f"{type(exc).__name__}: "
            f"{exc}"
        )


# ============================================================
# Global counts
# ============================================================


async def count_embeddings(
    engine,
) -> tuple[int, int, int]:
    """
    Return:

        total chunks
        embedded chunks
        remaining chunks
    """

    async with (
        engine.connect()
        as connection
    ):
        result = (
            await connection.execute(
                text(
                    """
                    SELECT
                        COUNT(*)
                            AS total_chunks,

                        COUNT(embedding)
                            AS embedded_chunks,

                        COUNT(*)
                        - COUNT(embedding)
                            AS remaining_chunks

                    FROM document_chunks
                    """
                )
            )
        )

        row = (
            result
            .mappings()
            .one()
        )

    return (
        int(
            row["total_chunks"]
        ),
        int(
            row["embedded_chunks"]
        ),
        int(
            row["remaining_chunks"]
        ),
    )
