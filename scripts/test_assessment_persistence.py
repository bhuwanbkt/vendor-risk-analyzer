from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from vendor_risk_analyzer.db.url import (
    get_database_url,
)
from vendor_risk_analyzer.assessments.service import (
    AssessmentPersistenceService,
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
            "Run vendor risk analysis and "
            "optionally persist the resulting "
            "assessment."
        )
    )

    parser.add_argument(
        "--vendor-id",
        required=True,
    )

    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Actually create the assessment "
            "and findings in PostgreSQL."
        ),
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

    persistence_service = (
        AssessmentPersistenceService()
    )

    try:
        print(
            "Running vendor "
            "risk analysis..."
        )

        print(
            "Vendor:",
            args.vendor_id,
        )

        print(
            "Write mode:",
            args.write,
        )

        print()

        async with (
            session_factory()
            as db
        ):
            analysis = (
                await risk_service
                .analyze_vendor(
                    db=db,
                    vendor_id=(
                        args.vendor_id
                    ),
                )
            )

            print(
                "Normalized findings:",
                (
                    analysis
                    .normalized_finding_count
                ),
            )

            for (
                index,
                finding,
            ) in enumerate(
                analysis.findings,
                start=1,
            ):
                print()

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
                    "  Evidence:",
                    len(
                        finding
                        .evidence_chunk_ids
                    ),
                    "chunk(s)",
                )

            if not args.write:
                print()
                print(
                    "DRY RUN: Nothing was "
                    "written to PostgreSQL."
                )

                print(
                    "PASS: Analysis is ready "
                    "for persistence."
                )

                return 0

            print()
            print(
                "Persisting assessment..."
            )

            persisted = (
                await persistence_service
                .persist(
                    db=db,
                    analysis=analysis,
                )
            )

        print()
        print(
            "================================"
        )

        print(
            "ASSESSMENT PERSISTED"
        )

        print(
            "================================"
        )

        print(
            "Assessment ID:",
            persisted.assessment_id,
        )

        print(
            "Vendor ID:",
            persisted.vendor_id,
        )

        print(
            "Status:",
            persisted.status,
        )

        print(
            "Type:",
            persisted.assessment_type,
        )

        print(
            "Findings:",
            persisted.finding_count,
        )

        for finding in (
            persisted.findings
        ):
            print()

            print(
                "Finding ID:",
                finding.finding_id,
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
                "  Severity:",
                finding.severity,
            )

            print(
                "  Primary chunk:",
                finding.primary_chunk_id,
            )

        print()
        print(
            "PASS: Assessment and "
            "findings committed."
        )

        return 0

    except Exception as exc:
        print()

        print(
            "FAIL: Assessment "
            "persistence test failed."
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