from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from vendor_risk_analyzer.db.url import (
    get_database_url,
)
from vendor_risk_analyzer.db.models import (
    Assessment,
)
from vendor_risk_analyzer.risk_policy.service import (
    POLICY_VERSION,
    RiskPolicyService,
)


BATCH_ALL = "all"


async def list_outdated_assessments(db) -> list:
    """Assessments not yet rated with the current policy version."""
    applied_version = func.coalesce(
        Assessment.extra_data["risk_policy_version"].astext,
        "",
    )
    result = await db.execute(
        select(
            Assessment.id,
            Assessment.overall_risk,
        )
        .where(applied_version != POLICY_VERSION)
        .order_by(Assessment.created_at, Assessment.id)
    )
    return list(result.all())


async def apply_to_all(
    *,
    session_factory,
    service,
    write: bool,
    out=print,
) -> tuple[int, int]:
    """
    Evaluate (dry run) or apply the current policy to every
    outdated assessment. Each assessment uses its own session
    and transaction, so one failure does not block the rest.

    Returns (succeeded, failed).
    """
    async with session_factory() as db:
        rows = await list_outdated_assessments(db)

    out("================================")
    out("RISK POLICY BATCH")
    out("================================")
    out("Policy:", POLICY_VERSION)
    out("Write mode:", write)
    out("Assessments to process:", len(rows))
    out()

    succeeded = 0
    failed = 0

    for assessment_id, current_risk in rows:
        try:
            async with session_factory() as db:
                if write:
                    evaluation = await service.apply_assessment(
                        db=db,
                        assessment_id=assessment_id,
                    )
                else:
                    evaluation = await service.evaluate_assessment(
                        db=db,
                        assessment_id=assessment_id,
                    )
        except Exception as exc:
            failed += 1
            out(
                f"FAIL {assessment_id}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        succeeded += 1
        out(
            f"OK   {assessment_id}: "
            f"overall {current_risk or 'unrated'} -> "
            f"{evaluation.proposed_overall_risk or 'unrated'} "
            f"({len(evaluation.decisions)} findings)"
        )

    out()
    out(
        f"{'Updated' if write else 'Ready to update'}: {succeeded}  "
        f"Failed: {failed}"
    )
    if not write:
        out("DRY RUN: No database rows were modified.")

    return succeeded, failed


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
        help=(
            "Assessment UUID, or 'all' for every "
            "assessment not on the current policy."
        ),
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

    if args.assessment_id.strip().lower() == BATCH_ALL:
        try:
            _, failed = await apply_to_all(
                session_factory=session_factory,
                service=service,
                write=args.write,
            )
            return 1 if failed else 0
        except Exception as exc:
            print()
            print("FAIL: Risk policy batch failed.")
            print(f"{type(exc).__name__}: {exc}")
            return 1
        finally:
            await engine.dispose()

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