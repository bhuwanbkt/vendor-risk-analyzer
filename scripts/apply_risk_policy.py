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
from vendor_risk_analyzer.risk_policy.service import (
    RiskPolicyService,
)


async def async_main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply deterministic risk policy "
            "to an existing assessment."
        )
    )

    parser.add_argument(
        "--assessment-id",
        required=True,
    )

    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Persist severity and overall "
            "risk decisions."
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

    service = RiskPolicyService()

    try:
        async with (
            session_factory()
            as db
        ):
            if args.write:
                evaluation = (
                    await service
                    .apply_assessment(
                        db=db,
                        assessment_id=(
                            args.assessment_id
                        ),
                    )
                )
            else:
                evaluation = (
                    await service
                    .evaluate_assessment(
                        db=db,
                        assessment_id=(
                            args.assessment_id
                        ),
                    )
                )

        print(
            "================================"
        )
        print(
            "RISK POLICY APPLICATION"
        )
        print(
            "================================"
        )

        print(
            "Assessment:",
            evaluation.assessment_id,
        )

        print(
            "Policy:",
            evaluation.policy_version,
        )

        print(
            "Write mode:",
            args.write,
        )

        print(
            "Overall risk:",
            evaluation.proposed_overall_risk,
        )

        print()

        for (
            index,
            decision,
        ) in enumerate(
            evaluation.decisions,
            start=1,
        ):
            print(
                f"Finding {index}"
            )

            print(
                "  Finding ID:",
                decision.finding_id,
            )

            print(
                "  Current severity:",
                decision.current_severity,
            )

            print(
                "  Proposed severity:",
                decision.proposed_severity,
            )

            print(
                "  Rule:",
                decision.rule_id,
            )

            print()

        if args.write:
            print(
                "PASS: Policy decisions "
                "were persisted."
            )
        else:
            print(
                "DRY RUN: No database "
                "rows were modified."
            )

            print(
                "PASS: Policy decisions "
                "are ready to apply."
            )

        return 0

    except Exception as exc:
        print()

        print(
            "FAIL: Risk policy "
            "application failed."
        )

        print(
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return 1

    finally:
        await engine.dispose()


def main() -> int:
    return asyncio.run(
        async_main()
    )


if __name__ == "__main__":
    sys.exit(
        main()
    )