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
from vendor_risk_analyzer.risk_policy.service import (
    RiskPolicyService,
)


async def async_main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate deterministic "
            "risk policy for an "
            "existing assessment."
        )
    )

    parser.add_argument(
        "--assessment-id",
        required=True,
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
            "RISK POLICY EVALUATION"
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
            "Proposed overall risk:",
            (
                evaluation
                .proposed_overall_risk
            ),
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
                "  Type:",
                decision.finding_type,
            )

            print(
                "  Category:",
                decision.category,
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

            print(
                "  Rationale:",
                decision.rationale,
            )

            print()

        print(
            "PASS: Policy evaluation "
            "completed."
        )

        print(
            "No database rows "
            "were modified."
        )

        return 0

    except Exception as exc:
        print()

        print(
            "FAIL: Risk policy "
            "evaluation failed."
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