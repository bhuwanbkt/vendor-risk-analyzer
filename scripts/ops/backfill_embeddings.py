from __future__ import annotations

import argparse
import asyncio

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from vendor_risk_analyzer.db.url import (
    get_database_url,
)
from vendor_risk_analyzer.embeddings.repository import (
    build_embedding_title,
    count_embeddings,
    fetch_chunks,
    finalize_document,
    get_document_embedding_state,
    mark_document_failed,
    normalize_metadata,
    save_embedding,
    update_document_status,
    verify_embedding,
)
from vendor_risk_analyzer.embeddings.service import (
    EmbeddingError,
    EmbeddingService,
)


# ============================================================
# Main worker
# ============================================================


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Safely backfill Gemini embeddings "
            "and maintain document lifecycle state."
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help=(
            "Maximum number of chunks "
            "with NULL embeddings to process."
        ),
    )

    parser.add_argument(
        "--document-id",
        type=str,
        default=None,
        help=(
            "Optional document UUID. "
            "When supplied, only that "
            "document is processed."
        ),
    )

    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Save generated embeddings "
            "and update document statuses. "
            "Without this flag the script "
            "is a dry run."
        ),
    )

    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=4.0,
        help=(
            "Seconds to wait between "
            "Gemini embedding requests."
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

    database_url = (
        get_database_url()
    )

    engine = (
        create_async_engine(
            database_url,
            poolclass=NullPool,
            pool_pre_ping=True,
        )
    )

    embedding_service = (
        EmbeddingService()
    )

    processed = 0
    saved = 0
    failed = False

    active_document_id: (
        str | None
    ) = None

    active_document_failed = False

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
            "Embedding worker"
        )

        print(
            "=============================="
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

        if args.document_id:
            print(
                f"Document filter:   "
                f"{args.document_id}"
            )

        print()

        chunks = await fetch_chunks(
            engine,
            limit=args.limit,
            document_id=(
                args.document_id
            ),
        )

        # ----------------------------------------------------
        # Nothing to process
        # ----------------------------------------------------

        if not chunks:
            print(
                "No chunks with NULL "
                "embeddings were found."
            )

            # If a specific document was requested,
            # verify whether it can safely be marked ready.
            if (
                args.write
                and args.document_id
            ):
                state = (
                    await get_document_embedding_state(
                        engine,
                        document_id=(
                            args.document_id
                        ),
                    )
                )

                if (
                    state["total_chunks"] > 0
                    and state[
                        "missing_embeddings"
                    ] == 0
                ):
                    await update_document_status(
                        engine,
                        document_id=(
                            args.document_id
                        ),
                        status="ready",
                    )

                    print(
                        "Document verified and "
                        "marked ready."
                    )

            return 0

        # ----------------------------------------------------
        # Process selected chunks
        # ----------------------------------------------------

        for index, chunk in enumerate(
            chunks,
            start=1,
        ):
            document_id = str(
                chunk["document_id"]
            )

            # ------------------------------------------------
            # Document transition
            # ------------------------------------------------

            if (
                document_id
                != active_document_id
            ):
                # We are moving to a different document.
                # Finalize the previous document first.
                if (
                    active_document_id
                    is not None
                    and args.write
                    and not active_document_failed
                ):
                    previous_status = (
                        await finalize_document(
                            engine,
                            document_id=(
                                active_document_id
                            ),
                        )
                    )

                    print()
                    print(
                        "Previous document status: "
                        f"{previous_status}"
                    )
                    print()

                active_document_id = (
                    document_id
                )

                active_document_failed = (
                    False
                )

                if args.write:
                    await update_document_status(
                        engine,
                        document_id=(
                            document_id
                        ),
                        status="embedding",
                    )

                    print()
                    print(
                        "Document status: embedding"
                    )

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
                f"{document_id}"
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

                if not args.write:
                    print(
                        "DRY RUN: embedding "
                        "was NOT written."
                    )

                else:
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
                            "Database vector "
                            "dimension verification "
                            "failed."
                        )

                    saved += 1

                    print(
                        "Saved to PostgreSQL."
                    )

                    print(
                        "Database dimensions: "
                        f"{verification['dimensions']}"
                    )

                    document_state = (
                        await get_document_embedding_state(
                            engine,
                            document_id=(
                                document_id
                            ),
                        )
                    )

                    print(
                        "Document embedding progress: "
                        f"{document_state['embedded_chunks']}"
                        "/"
                        f"{document_state['total_chunks']}"
                    )

            # ------------------------------------------------
            # Embedding provider failure
            # ------------------------------------------------

            except EmbeddingError as exc:
                failed = True

                active_document_failed = (
                    True
                )

                print()
                print(
                    "EMBEDDING ERROR"
                )

                print(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                if args.write:
                    await mark_document_failed(
                        engine,
                        document_id=(
                            document_id
                        ),
                    )

                    print(
                        "Document status: "
                        "embedding_failed"
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

            # ------------------------------------------------
            # Database / unexpected failure
            # ------------------------------------------------

            except Exception as exc:
                failed = True

                active_document_failed = (
                    True
                )

                print()
                print(
                    "BACKFILL ERROR"
                )

                print(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                if args.write:
                    await mark_document_failed(
                        engine,
                        document_id=(
                            document_id
                        ),
                    )

                    print(
                        "Document status: "
                        "embedding_failed"
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

            # ------------------------------------------------
            # Provider pacing
            # ------------------------------------------------

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

        # ----------------------------------------------------
        # Finalize last active document
        # ----------------------------------------------------

        if (
            active_document_id
            is not None
            and args.write
            and not active_document_failed
        ):
            final_status = (
                await finalize_document(
                    engine,
                    document_id=(
                        active_document_id
                    ),
                )
            )

            print()
            print(
                "Final document status: "
                f"{final_status}"
            )

        # ----------------------------------------------------
        # Final summary
        # ----------------------------------------------------

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
            "Embedding worker summary"
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
                "to resume from chunks where "
                "embedding IS NULL."
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