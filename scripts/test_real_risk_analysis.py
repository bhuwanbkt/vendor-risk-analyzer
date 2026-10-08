from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from backfill_embeddings import (
    get_database_url,
)
from vendor_risk_analyzer.embeddings.service import (
    EmbeddingService,
)
from vendor_risk_analyzer.retrieval.service import (
    SemanticRetriever,
)
from vendor_risk_analyzer.risk_analysis.service import (
    RiskAnalysisService,
)


async def async_main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Test the reusable vendor "
            "risk-analysis service."
        )
    )

    parser.add_argument(
        "--vendor-id",
        required=True,
        help="Vendor UUID to analyze.",
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
            expire_on_commit=False,
        )
    )

    embedding_service = (
        EmbeddingService()
    )

    retriever = SemanticRetriever(
        embedding_service
    )

    risk_service = (
        RiskAnalysisService(
            retriever=retriever
        )
    )

    try:
        print(
            "Testing reusable "
            "RiskAnalysisService..."
        )

        print(
            "Vendor:",
            args.vendor_id,
        )

        print(
            "Model:",
            risk_service.model,
        )

        print()

        async with (
            session_factory()
            as db
        ):
            result = (
                await risk_service
                .analyze_vendor(
                    db=db,
                    vendor_id=(
                        args.vendor_id
                    ),
                )
            )

        print(
            "================================"
        )

        print(
            "VENDOR RISK ANALYSIS"
        )

        print(
            "================================"
        )

        print()

        print(
            "Summary:"
        )

        print(
            result.summary
        )

        print()

        print(
            "Raw findings:",
            result.raw_finding_count,
        )

        print(
            "Normalized findings:",
            (
                result
                .normalized_finding_count
            ),
        )

        print()

        evidence_lookup = {
            evidence.chunk_id:
                evidence
            for evidence
            in result.evidence
        }

        for (
            index,
            finding,
        ) in enumerate(
            result.findings,
            start=1,
        ):
            print(
                f"Finding {index}"
            )

            print(
                "  Type:",
                finding.finding_type,
            )

            print(
                "  Category:",
                finding.category,
            )

            print(
                "  Title:",
                finding.title,
            )

            print(
                "  Confidence:",
                finding.confidence,
            )

            print(
                "  Description:",
                finding.description,
            )

            print(
                "  Evidence:"
            )

            for chunk_id in (
                finding
                .evidence_chunk_ids
            ):
                source = (
                    evidence_lookup[
                        chunk_id
                    ]
                )

                print(
                    "    Chunk:",
                    chunk_id,
                )

                print(
                    "    Document:",
                    source.document_id,
                )

                print(
                    "    Sequence:",
                    source.sequence,
                )

                print(
                    "    Similarity:",
                    (
                        f"{source.similarity:.4f}"
                    ),
                )

            print()

        print(
            "PASS: Reusable "
            "RiskAnalysisService "
            "completed successfully."
        )

        print()

        print(
            "No assessments or findings "
            "were written to the database."
        )

        return 0

    except Exception as exc:
        print()

        print(
            "FAIL: Risk analysis "
            "service test failed."
        )

        print(
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return 1

    finally:
        risk_service.close()

        await embedding_service.close()

        await engine.dispose()


def main() -> int:
    return asyncio.run(
        async_main()
    )


if __name__ == "__main__":
    sys.exit(
        main()
    )