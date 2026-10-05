from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from vendor_risk_analyzer.embeddings.service import EmbeddingService


def get_database_url() -> str:
    """
    Read DATABASE_URL directly.

    This script does not load the complete application Settings
    because it does not need ZITADEL, storage, or session settings.
    """

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is required."
        )

    return normalize_database_url(database_url)


def normalize_database_url(database_url: str) -> str:
    """
    Normalize a Neon PostgreSQL URL for SQLAlchemy + asyncpg.
    """

    database_url = database_url.strip()

    if database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://",
            "postgresql://",
            1,
        )

    if database_url.startswith("postgresql+psycopg://"):
        database_url = database_url.replace(
            "postgresql+psycopg://",
            "postgresql://",
            1,
        )

    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace(
            "postgresql+asyncpg://",
            "postgresql://",
            1,
        )

    parsed = urlsplit(database_url)

    query_params = dict(
        parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
    )

    # Neon may provide this, but asyncpg does not need it here.
    query_params.pop(
        "channel_binding",
        None,
    )

    # Convert sslmode=require to ssl=require for asyncpg.
    sslmode = query_params.pop(
        "sslmode",
        None,
    )

    if sslmode and "ssl" not in query_params:
        query_params["ssl"] = sslmode

    query_params.setdefault(
        "prepared_statement_cache_size",
        "0",
    )

    return urlunsplit(
        (
            "postgresql+asyncpg",
            parsed.netloc,
            parsed.path,
            urlencode(query_params),
            parsed.fragment,
        )
    )


def normalize_metadata(
    value: Any,
) -> dict[str, Any]:
    """
    PostgreSQL JSONB normally comes back as a Python dict.

    This fallback also handles a JSON string safely.
    """

    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(value)

            if isinstance(parsed, dict):
                return parsed

        except json.JSONDecodeError:
            pass

    return {}


def build_embedding_title(
    metadata: dict[str, Any],
) -> str | None:
    """
    Build a useful structural title for the embedding.

    Priority:
      heading_path
      table_title
      caption
      sheet_name
    """

    heading_path = metadata.get(
        "heading_path"
    )

    if isinstance(heading_path, list):
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
        value = metadata.get(key)

        if isinstance(value, str):
            value = value.strip()

            if value:
                return value

    return None


def vector_to_pgvector_literal(
    embedding: list[float],
) -> str:
    """
    Convert Python floats into pgvector text format:

        [0.1,-0.2,0.3,...]
    """

    return (
        "["
        + ",".join(
            format(value, ".10g")
            for value in embedding
        )
        + "]"
    )


async def fetch_chunks(
    engine,
    *,
    limit: int,
    document_id: str | None,
) -> list[dict[str, Any]]:
    """
    Fetch chunks that do not yet have embeddings.
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
            AND document_id = CAST(:document_id AS uuid)
        """

        params["document_id"] = document_id

    sql += """
        ORDER BY document_id, sequence
        LIMIT :limit
    """

    async with engine.connect() as connection:
        result = await connection.execute(
            text(sql),
            params,
        )

        return [
            dict(row)
            for row in result.mappings().all()
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
    Save one embedding.

    The WHERE embedding IS NULL condition protects us from
    accidentally overwriting an existing vector.
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
            embedding = CAST(:embedding AS vector),
            metadata =
                COALESCE(metadata, '{}'::jsonb)
                || jsonb_build_object(
                    'embedding_provider',
                    'google',
                    'embedding_model',
                    :embedding_model,
                    'embedding_dimensions',
                    :embedding_dimensions
                ),
            updated_at = NOW()
        WHERE id = CAST(:chunk_id AS uuid)
          AND embedding IS NULL
        """
    )

    async with engine.begin() as connection:
        result = await connection.execute(
            sql,
            {
                "embedding": embedding_literal,
                "chunk_id": chunk_id,
                "embedding_model": model,
                "embedding_dimensions": dimensions,
            },
        )

        if result.rowcount != 1:
            raise RuntimeError(
                "Expected to update exactly one chunk, "
                f"but updated {result.rowcount}."
            )


async def verify_embedding(
    engine,
    *,
    chunk_id: str,
) -> dict[str, Any]:
    """
    Verify the vector exists in PostgreSQL after writing it.
    """

    sql = text(
        """
        SELECT
            id::text AS id,
            embedding IS NOT NULL AS has_embedding,
            vector_dims(embedding) AS dimensions,
            metadata
        FROM document_chunks
        WHERE id = CAST(:chunk_id AS uuid)
        """
    )

    async with engine.connect() as connection:
        result = await connection.execute(
            sql,
            {
                "chunk_id": chunk_id,
            },
        )

        row = result.mappings().one()

        return dict(row)


async def count_embeddings(
    engine,
) -> tuple[int, int]:
    """
    Return:
        total chunks
        chunks that already have embeddings
    """

    result = None

    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_chunks,
                    COUNT(embedding) AS embedded_chunks
                FROM document_chunks
                """
            )
        )

        row = result.mappings().one()

    return (
        int(row["total_chunks"]),
        int(row["embedded_chunks"]),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill Gemini embeddings for existing "
            "document_chunks."
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum number of chunks to process.",
    )

    parser.add_argument(
        "--document-id",
        type=str,
        default=None,
        help="Optional document UUID.",
    )

    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Actually save embeddings to PostgreSQL. "
            "Without this flag the script is a dry run."
        ),
    )

    args = parser.parse_args()

    if args.limit < 1:
        raise ValueError(
            "--limit must be at least 1."
        )

    # Safety protection for this first implementation.
    if args.limit > 10:
        raise ValueError(
            "--limit cannot exceed 10 during "
            "the initial validation stage."
        )

    database_url = get_database_url()

    engine = create_async_engine(
        database_url,
        poolclass=NullPool,
        pool_pre_ping=True,
    )

    embedding_service = EmbeddingService()

    try:
        total_chunks, embedded_before = (
            await count_embeddings(engine)
        )

        print()
        print("Embedding backfill")
        print("------------------")
        print(
            f"Total chunks: {total_chunks}"
        )
        print(
            f"Already embedded: {embedded_before}"
        )
        print(
            f"Mode: {'WRITE' if args.write else 'DRY RUN'}"
        )
        print(
            f"Limit: {args.limit}"
        )
        print()

        chunks = await fetch_chunks(
            engine,
            limit=args.limit,
            document_id=args.document_id,
        )

        if not chunks:
            print(
                "No chunks with NULL embeddings were found."
            )
            return

        for index, chunk in enumerate(
            chunks,
            start=1,
        ):
            metadata = normalize_metadata(
                chunk["metadata"]
            )

            title = build_embedding_title(
                metadata
            )

            content = str(
                chunk["content"]
            ).strip()

            print(
                f"[{index}/{len(chunks)}]"
            )
            print(
                f"Chunk ID: {chunk['id']}"
            )
            print(
                f"Document ID: {chunk['document_id']}"
            )
            print(
                f"Sequence: {chunk['sequence']}"
            )
            print(
                f"Title: {title or '(none)'}"
            )

            preview = content.replace(
                "\n",
                " ",
            )[:200]

            print(
                f"Content preview: {preview}"
            )

            embedding = (
                await embedding_service.embed_document(
                    content=content,
                    title=title,
                )
            )

            print(
                f"Generated dimensions: {len(embedding)}"
            )

            if not args.write:
                print(
                    "DRY RUN: embedding was NOT written "
                    "to PostgreSQL."
                )
                print()
                continue

            await save_embedding(
                engine,
                chunk_id=chunk["id"],
                embedding=embedding,
                model=embedding_service.model,
                dimensions=embedding_service.dimensions,
            )

            verification = (
                await verify_embedding(
                    engine,
                    chunk_id=chunk["id"],
                )
            )

            print(
                "Saved to PostgreSQL."
            )
            print(
                "Database has embedding: "
                f"{verification['has_embedding']}"
            )
            print(
                "Database dimensions: "
                f"{verification['dimensions']}"
            )
            print()

        _, embedded_after = (
            await count_embeddings(engine)
        )

        print("------------------")
        print(
            f"Embedded before: {embedded_before}"
        )
        print(
            f"Embedded after:  {embedded_after}"
        )

        if args.write:
            print(
                f"New embeddings saved: "
                f"{embedded_after - embedded_before}"
            )
        else:
            print(
                "Dry run completed. Database was not modified."
            )

    finally:
        await embedding_service.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())