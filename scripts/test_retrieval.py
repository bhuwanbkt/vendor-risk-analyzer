from __future__ import annotations

import argparse
import asyncio
import os
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from vendor_risk_analyzer.embeddings.service import (
    EmbeddingService,
)
from vendor_risk_analyzer.retrieval.service import (
    SemanticRetriever,
)


def get_database_url() -> str:
    database_url = os.getenv(
        "DATABASE_URL"
    )

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is required."
        )

    return normalize_database_url(
        database_url
    )


def normalize_database_url(
    database_url: str,
) -> str:

    database_url = (
        database_url.strip()
    )

    if database_url.startswith(
        "postgres://"
    ):
        database_url = (
            database_url.replace(
                "postgres://",
                "postgresql://",
                1,
            )
        )

    if database_url.startswith(
        "postgresql+psycopg://"
    ):
        database_url = (
            database_url.replace(
                "postgresql+psycopg://",
                "postgresql://",
                1,
            )
        )

    if database_url.startswith(
        "postgresql+asyncpg://"
    ):
        database_url = (
            database_url.replace(
                "postgresql+asyncpg://",
                "postgresql://",
                1,
            )
        )

    parsed = urlsplit(
        database_url
    )

    query_params = dict(
        parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
    )

    query_params.pop(
        "channel_binding",
        None,
    )

    sslmode = query_params.pop(
        "sslmode",
        None,
    )

    if (
        sslmode
        and "ssl"
        not in query_params
    ):
        query_params["ssl"] = (
            sslmode
        )

    query_params.setdefault(
        "prepared_statement_cache_size",
        "0",
    )

    return urlunsplit(
        (
            "postgresql+asyncpg",
            parsed.netloc,
            parsed.path,
            urlencode(
                query_params
            ),
            parsed.fragment,
        )
    )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Test semantic retrieval "
            "against pgvector."
        )
    )

    parser.add_argument(
        "--query",
        required=True,
        help="Natural-language search query.",
    )

    parser.add_argument(
        "--document-id",
        default=None,
        help=(
            "Optional document UUID "
            "to restrict retrieval."
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
    )

    args = parser.parse_args()

    database_url = (
        get_database_url()
    )

    engine = create_async_engine(
        database_url,
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
        async with (
            session_factory()
            as db
        ):
            results = (
                await retriever.search(
                    db=db,
                    query=args.query,
                    limit=args.limit,
                    document_id=(
                        args.document_id
                    ),
                )
            )

        print()
        print(
            "Semantic retrieval test"
        )

        print(
            "================================"
        )

        print(
            f"Query: {args.query}"
        )

        if args.document_id:
            print(
                "Document: "
                f"{args.document_id}"
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
                "No matching chunks found."
            )

            return 1

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