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

from vendor_risk_analyzer.embeddings.service import (
    EmbeddingError,
    EmbeddingService,
)


def get_database_url() -> str:
    """
    Read DATABASE_URL directly from the environment.

    This script does not load the application's complete Settings
    because it only needs database and embedding configuration.
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

    if database_url.startswith(
        "postgresql+psycopg://"
    ):
        database_url = database_url.replace(
            "postgresql+psycopg://",
            "postgresql://",
            1,
        )

    if database_url.startswith(
        "postgresql+asyncpg://"
    ):
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

    # Neon may provide channel_binding.
    # asyncpg does not need it here.
    query_params.pop(
        "channel_binding",
        None,
    )

    # Convert:
    #
    # sslmode=require
    #
    # to:
    #
    # ssl=require
    #
    # for asyncpg.
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
    Convert metadata into a normal Python dictionary.
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
    Convert Python floats into pgvector text format.

    Example:

        [0.1,-0.2,0.3]
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
            AND document_id = CAST(
                :document_id AS uuid
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

    async with engine.connect() as connection:
        result = await connection.execute(
            text(sql),
            params,
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

    Each call uses its own database transaction.

    This is intentional:
    if chunk 20 later fails, chunks 1-19 remain committed.
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
            id = CAST(
                :chunk_id
                AS uuid
            )

            AND embedding IS NULL
        """
    )

    async with engine.begin() as connection:
        result = await connection.execute(
            sql,
            {
                "embedding": (
                    embedding_literal
                ),
                "chunk_id": chunk_id,
                "embedding_model": model,
                "embedding_dimensions": (
                    dimensions
                ),
            },
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
    Verify that PostgreSQL contains the vector.
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

        WHERE id = CAST(
            :chunk_id AS uuid
        )
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
) -> tuple[int, int, int]:
    """
    Return:

        total chunks
        embedded chunks
        remaining chunks
    """

    async with engine.connect() as connection:
        result = await connection.execute(
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

        row = result.mappings().one()

    return (
        int(row["total_chunks"]),
        int(row["embedded_chunks"]),
        int(row["remaining_chunks"]),
    )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Safely backfill Gemini embeddings "
            "for document_chunks."
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help=(
            "Maximum number of NULL-embedding "
            "chunks to process."
        ),
    )

    parser.add_argument(
        "--document-id",
        type=str,
        default=None,
        help=(
            "Optional document UUID. "
            "If supplied, only that document "
            "is processed."
        ),
    )

    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Save generated embeddings to "
            "PostgreSQL. Without this flag "
            "the script is a dry run."
        ),
    )

    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=4.0,
        help=(
            "Seconds to wait between Gemini "
            "embedding requests."
        ),
    )

    args = parser.parse_args()

    if args.limit < 1:
        raise ValueError(
            "--limit must be at least 1."
        )

    if args.delay_seconds < 0:
        raise ValueError(
            "--delay-seconds cannot be negative."
        )

    database_url = get_database_url()

    engine = create_async_engine(
        database_url,
        poolclass=NullPool,
        pool_pre_ping=True,
    )

    embedding_service = (
        EmbeddingService()
    )

    processed = 0
    saved = 0
    failed = False

    try:
        (
            total_chunks,
            embedded_before,
            remaining_before,
        ) = await count_embeddings(
            engine
        )

        print()
        print(
            "Embedding backfill"
        )
        print(
            "=================="
        )

        print(
            f"Total chunks:      "
            f"{total_chunks}"
        )

        print(
            f"Already embedded:  "
            f"{embedded_before}"
        )

        print(
            f"Remaining:         "
            f"{remaining_before}"
        )

        print(
            "Mode:              "
            f"{'WRITE' if args.write else 'DRY RUN'}"
        )

        print(
            f"Batch limit:       "
            f"{args.limit}"
        )

        print(
            f"Delay:             "
            f"{args.delay_seconds} seconds"
        )

        print()

        chunks = await fetch_chunks(
            engine,
            limit=args.limit,
            document_id=(
                args.document_id
            ),
        )

        if not chunks:
            print(
                "No chunks with NULL "
                "embeddings were found."
            )

            return 0

        for index, chunk in enumerate(
            chunks,
            start=1,
        ):
            metadata = (
                normalize_metadata(
                    chunk["metadata"]
                )
            )

            title = (
                build_embedding_title(
                    metadata
                )
            )

            content = str(
                chunk["content"]
            ).strip()

            preview = (
                content
                .replace(
                    "\n",
                    " ",
                )
                [:200]
            )

            print(
                "--------------------------------"
            )

            print(
                f"[{index}/{len(chunks)}]"
            )

            print(
                f"Chunk ID: "
                f"{chunk['id']}"
            )

            print(
                f"Document ID: "
                f"{chunk['document_id']}"
            )

            print(
                f"Sequence: "
                f"{chunk['sequence']}"
            )

            print(
                f"Title: "
                f"{title or '(none)'}"
            )

            print(
                f"Content preview: "
                f"{preview}"
            )

            try:
                embedding = (
                    await embedding_service
                    .embed_document(
                        content=content,
                        title=title,
                    )
                )

                processed += 1

                print(
                    "Generated dimensions: "
                    f"{len(embedding)}"
                )

                if args.write:
                    await save_embedding(
                        engine,
                        chunk_id=(
                            chunk["id"]
                        ),
                        embedding=embedding,
                        model=(
                            embedding_service.model
                        ),
                        dimensions=(
                            embedding_service.dimensions
                        ),
                    )

                    verification = (
                        await verify_embedding(
                            engine,
                            chunk_id=(
                                chunk["id"]
                            ),
                        )
                    )

                    if not verification[
                        "has_embedding"
                    ]:
                        raise RuntimeError(
                            "Database verification "
                            "failed: embedding is NULL."
                        )

                    if (
                        verification[
                            "dimensions"
                        ]
                        != embedding_service.dimensions
                    ):
                        raise RuntimeError(
                            "Database vector dimension "
                            "verification failed."
                        )

                    saved += 1

                    print(
                        "Saved to PostgreSQL."
                    )

                    print(
                        "Database dimensions: "
                        f"{verification['dimensions']}"
                    )

                else:
                    print(
                        "DRY RUN: embedding was "
                        "NOT written."
                    )

            except EmbeddingError as exc:
                failed = True

                print()
                print(
                    "EMBEDDING ERROR"
                )

                print(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                print()
                print(
                    "Stopping the batch safely."
                )

                print(
                    "Previously saved embeddings "
                    "remain committed."
                )

                break

            except Exception as exc:
                failed = True

                print()
                print(
                    "BACKFILL ERROR"
                )

                print(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                print()
                print(
                    "Stopping the batch safely."
                )

                print(
                    "Previously saved embeddings "
                    "remain committed."
                )

                break

            # Wait before the next Gemini request.
            if (
                index < len(chunks)
                and args.delay_seconds > 0
            ):
                print(
                    "Waiting "
                    f"{args.delay_seconds} "
                    "seconds before next request..."
                )

                await asyncio.sleep(
                    args.delay_seconds
                )

        (
            total_after,
            embedded_after,
            remaining_after,
        ) = await count_embeddings(
            engine
        )

        print()
        print(
            "================================"
        )

        print(
            "Backfill summary"
        )

        print(
            "================================"
        )

        print(
            f"Total chunks:       "
            f"{total_after}"
        )

        print(
            f"Embedded before:    "
            f"{embedded_before}"
        )

        print(
            f"Embedded after:     "
            f"{embedded_after}"
        )

        print(
            f"Remaining:          "
            f"{remaining_after}"
        )

        print(
            f"Generated this run: "
            f"{processed}"
        )

        if args.write:
            print(
                f"Saved this run:     "
                f"{saved}"
            )

        if failed:
            print()
            print(
                "Batch stopped early."
            )

            print(
                "Run the same command again "
                "later to resume from the next "
                "NULL embedding."
            )

            return 2

        print()

        if args.write:
            print(
                "Batch completed successfully."
            )

        else:
            print(
                "Dry run completed successfully."
            )

        return 0

    finally:
        await embedding_service.close()
        await engine.dispose()


if __name__ == "__main__":
    exit_code = asyncio.run(
        main()
    )

    raise SystemExit(
        exit_code
    )