from __future__ import annotations

import argparse
import asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from vendor_risk_analyzer.db.url import (
    get_database_url,
)
from vendor_risk_analyzer.embeddings.service import (
    EmbeddingService,
)
from vendor_risk_analyzer.retrieval.service import (
    DocumentNotFoundError,
    SemanticRetriever,
    VendorNotFoundError,
)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Test vendor-scoped semantic "
            "retrieval against pgvector."
        )
    )

    parser.add_argument(
        "--query",
        required=True,
    )

    parser.add_argument(
        "--vendor-id",
        required=True,
    )

    parser.add_argument(
        "--document-id",
        default=None,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
    )

    args = parser.parse_args()

    engine = create_async_engine(
        get_database_url(),
        poolclass=NullPool,
        pool_pre_ping=True,
    )

    session_factory = (
        async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    )

    embedding_service = (
        EmbeddingService()
    )

    retriever = SemanticRetriever(
        embedding_service
    )

    try:
        try:
            async with (
                session_factory() as db
            ):
                results = (
                    await retriever.search(
                        db=db,
                        query=args.query,
                        vendor_id=(
                            args.vendor_id
                        ),
                        document_id=(
                            args.document_id
                        ),
                        limit=args.limit,
                    )
                )

        except (
            VendorNotFoundError,
            DocumentNotFoundError,
            ValueError,
        ) as exc:
            print(
                f"ERROR: {exc}"
            )
            return 2

        print()
        print(
            "Vendor semantic retrieval test"
        )
        print(
            "================================"
        )

        print(
            f"Query: {args.query}"
        )

        print(
            f"Vendor: {args.vendor_id}"
        )

        if args.document_id:
            print(
                "Document: "
                f"{args.document_id}"
            )
        else:
            print(
                "Document scope: "
                "ALL READY DOCUMENTS "
                "FOR VENDOR"
            )

        print(
            f"Results: {len(results)}"
        )

        print()

        for rank, result in enumerate(
            results,
            start=1,
        ):
            print(
                "--------------------------------"
            )

            print(
                f"Rank: {rank}"
            )

            print(
                "Chunk ID: "
                f"{result.chunk_id}"
            )

            print(
                "Document ID: "
                f"{result.document_id}"
            )

            print(
                "Sequence: "
                f"{result.sequence}"
            )

            print(
                "Similarity: "
                f"{result.similarity:.4f}"
            )

            print(
                "Cosine distance: "
                f"{result.cosine_distance:.4f}"
            )

            heading_path = (
                result.metadata.get(
                    "heading_path"
                )
            )

            if heading_path:
                print(
                    "Heading path: "
                    f"{heading_path}"
                )

            print()
            print(
                result.content
            )
            print()

        if not results:
            print(
                "No matching chunks found "
                "for this vendor."
            )

        return 0

    finally:
        await embedding_service.close()

        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(
        asyncio.run(
            main()
        )
    )