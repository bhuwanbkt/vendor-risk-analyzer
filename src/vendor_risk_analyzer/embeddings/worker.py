from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from scripts.backfill_embeddings import (
    build_embedding_title,
    fetch_chunks,
    finalize_document,
    get_database_url,
    get_document_embedding_state,
    mark_document_failed,
    normalize_metadata,
    save_embedding,
    verify_embedding,
)
from vendor_risk_analyzer.embeddings.service import (
    EmbeddingError,
    EmbeddingService,
)


# Use Uvicorn's logger so messages appear
logger = logging.getLogger(
    "uvicorn.error"
)


def _get_int_env(
    name: str,
    default: int,
    *,
    minimum: int,
) -> int:
    raw_value = os.getenv(name)

    if raw_value is None:
        return default

    value = int(raw_value)

    if value < minimum:
        raise ValueError(
            f"{name} must be at least {minimum}."
        )

    return value


def _get_float_env(
    name: str,
    default: float,
    *,
    minimum: float,
) -> float:
    raw_value = os.getenv(name)

    if raw_value is None:
        return default

    value = float(raw_value)

    if value < minimum:
        raise ValueError(
            f"{name} must be at least {minimum}."
        )

    return value


async def recover_stale_documents(
    engine,
    *,
    stale_minutes: int,
) -> int:
    """
    Recover documents left in `embedding`
    after an interrupted worker/container.
    """

    sql = text(
        """
        UPDATE documents
        SET
            status = 'embedding_pending',
            updated_at = NOW()
        WHERE
            status = 'embedding'
            AND updated_at < (
                NOW()
                - (
                    CAST(:stale_minutes AS integer)
                    * INTERVAL '1 minute'
                )
            )
        """
    )

    async with engine.begin() as connection:
        result = await connection.execute(
            sql,
            {
                "stale_minutes":
                    stale_minutes,
            },
        )

    return int(
        result.rowcount or 0
    )


async def claim_next_document(
    engine,
) -> str | None:
    """
    Atomically claim one document waiting
    for embeddings.

    SKIP LOCKED prevents two workers from
    claiming the same document.
    """

    sql = text(
        """
        WITH candidate AS (
            SELECT
                d.id
            FROM documents d
            WHERE
                d.status =
                    'embedding_pending'
                AND EXISTS (
                    SELECT 1
                    FROM document_chunks dc
                    WHERE
                        dc.document_id = d.id
                )
            ORDER BY
                d.updated_at ASC,
                d.created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )

        UPDATE documents AS d
        SET
            status = 'embedding',
            updated_at = NOW()
        FROM candidate
        WHERE
            d.id = candidate.id
        RETURNING
            d.id::text AS document_id
        """
    )

    async with engine.begin() as connection:
        result = await connection.execute(
            sql
        )

        row = (
            result
            .mappings()
            .one_or_none()
        )

    if row is None:
        return None

    return str(
        row["document_id"]
    )


async def process_document(
    engine,
    embedding_service: EmbeddingService,
    *,
    document_id: str,
    batch_size: int,
    delay_seconds: float,
) -> str:
    """
    Embed every remaining chunk for one
    claimed document.
    """

    logger.info(
        "Embedding worker started "
        "document %s",
        document_id,
    )

    try:
        while True:
            chunks = await fetch_chunks(
                engine,
                limit=batch_size,
                document_id=document_id,
            )

            if not chunks:
                final_status = (
                    await finalize_document(
                        engine,
                        document_id=(
                            document_id
                        ),
                    )
                )

                logger.info(
                    "Embedding worker finalized "
                    "document %s as %s",
                    document_id,
                    final_status,
                )

                return final_status

            for chunk in chunks:
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

                logger.info(
                    "Generating embedding for "
                    "document %s chunk %s "
                    "sequence %s",
                    document_id,
                    chunk["id"],
                    chunk["sequence"],
                )

                embedding = (
                    await embedding_service
                    .embed_document(
                        content=content,
                        title=title,
                    )
                )

                await save_embedding(
                    engine,
                    chunk_id=str(
                        chunk["id"]
                    ),
                    embedding=embedding,
                    model=(
                        embedding_service.model
                    ),
                    dimensions=(
                        embedding_service
                        .dimensions
                    ),
                )

                verification = (
                    await verify_embedding(
                        engine,
                        chunk_id=str(
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
                    !=
                    embedding_service
                    .dimensions
                ):
                    raise RuntimeError(
                        "Database vector dimension "
                        "verification failed."
                    )

                state = (
                    await
                    get_document_embedding_state(
                        engine,
                        document_id=(
                            document_id
                        ),
                    )
                )

                logger.info(
                    "Document %s embedding "
                    "progress: %s/%s",
                    document_id,
                    state[
                        "embedded_chunks"
                    ],
                    state[
                        "total_chunks"
                    ],
                )

                if (
                    state[
                        "missing_embeddings"
                    ] > 0
                    and delay_seconds > 0
                ):
                    logger.info(
                        "Waiting %.1f seconds "
                        "before next embedding "
                        "request.",
                        delay_seconds,
                    )

                    await asyncio.sleep(
                        delay_seconds
                    )

    except asyncio.CancelledError:
        logger.info(
            "Embedding worker cancellation "
            "received for document %s",
            document_id,
        )

        try:
            final_status = (
                await finalize_document(
                    engine,
                    document_id=document_id,
                )
            )

            logger.info(
                "Document %s restored to %s "
                "during worker shutdown.",
                document_id,
                final_status,
            )

        except Exception:
            logger.exception(
                "Could not restore lifecycle "
                "state for document %s "
                "during shutdown.",
                document_id,
            )

        raise

    except EmbeddingError:
        logger.exception(
            "Embedding provider failed "
            "for document %s",
            document_id,
        )

        await mark_document_failed(
            engine,
            document_id=document_id,
        )

        logger.error(
            "Document %s marked "
            "embedding_failed.",
            document_id,
        )

        return "embedding_failed"

    except Exception:
        logger.exception(
            "Automatic embedding failed "
            "for document %s",
            document_id,
        )

        await mark_document_failed(
            engine,
            document_id=document_id,
        )

        logger.error(
            "Document %s marked "
            "embedding_failed.",
            document_id,
        )

        return "embedding_failed"


async def run_embedding_worker() -> None:
    """
    Long-running lightweight worker.

    Polls PostgreSQL for documents in
    embedding_pending.
    """

    poll_seconds = _get_float_env(
        "EMBEDDING_WORKER_POLL_SECONDS",
        10.0,
        minimum=1.0,
    )

    batch_size = _get_int_env(
        "EMBEDDING_WORKER_BATCH_SIZE",
        20,
        minimum=1,
    )

    delay_seconds = _get_float_env(
        "EMBEDDING_WORKER_DELAY_SECONDS",
        4.0,
        minimum=0.0,
    )

    stale_minutes = _get_int_env(
        "EMBEDDING_WORKER_STALE_MINUTES",
        30,
        minimum=1,
    )

    engine = create_async_engine(
        get_database_url(),
        poolclass=NullPool,
        pool_pre_ping=True,
    )

    embedding_service = (
        EmbeddingService()
    )

    try:
        recovered = (
            await recover_stale_documents(
                engine,
                stale_minutes=(
                    stale_minutes
                ),
            )
        )

        if recovered:
            logger.warning(
                "Recovered %s stale "
                "embedding document(s).",
                recovered,
            )

        logger.info(
            "Automatic embedding worker "
            "started. poll=%.1fs "
            "batch=%s delay=%.1fs",
            poll_seconds,
            batch_size,
            delay_seconds,
        )

        while True:
            try:
                document_id = (
                    await claim_next_document(
                        engine
                    )
                )

                if document_id is None:
                    await asyncio.sleep(
                        poll_seconds
                    )

                    continue

                logger.info(
                    "Claimed document %s "
                    "for automatic embedding.",
                    document_id,
                )

                final_status = (
                    await process_document(
                        engine,
                        embedding_service,
                        document_id=(
                            document_id
                        ),
                        batch_size=batch_size,
                        delay_seconds=(
                            delay_seconds
                        ),
                    )
                )

                logger.info(
                    "Automatic embedding "
                    "processing completed for "
                    "document %s with status %s.",
                    document_id,
                    final_status,
                )

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception(
                    "Embedding worker loop "
                    "error. Retrying after "
                    "poll delay."
                )

                await asyncio.sleep(
                    poll_seconds
                )

    except asyncio.CancelledError:
        logger.info(
            "Automatic embedding worker "
            "stopped."
        )

        raise

    finally:
        await embedding_service.close()

        await engine.dispose()