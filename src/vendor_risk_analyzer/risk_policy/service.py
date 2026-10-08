from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from vendor_risk_analyzer.db.models import (
    Assessment,
    Finding,
)
from vendor_risk_analyzer.risk_policy.schemas import (
    PolicyDecision,
    RiskPolicyEvaluation,
)


logger = logging.getLogger(
    "uvicorn.error"
)


POLICY_VERSION = (
    "vendor-risk-policy-v1"
)


SEVERITY_RANK = {
    "unrated": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


POLICY_RULES = {
    (
        "contradiction",
        "incident_response",
    ): {
        "severity": "medium",
        "rule_id": (
            "VRP-IR-CONTRADICTION-001"
        ),
        "rationale": (
            "Conflicting incident-response "
            "requirements require analyst "
            "review because they may create "
            "operational ambiguity."
        ),
    },

    (
        "explicit_risk",
        "business_continuity",
    ): {
        "severity": "medium",
        "rule_id": (
            "VRP-BC-EXPLICIT-001"
        ),
        "rationale": (
            "An explicitly documented open "
            "business-continuity issue "
            "requires remediation tracking."
        ),
    },
}


class RiskPolicyError(
    RuntimeError
):
    """Risk policy evaluation failed."""


class RiskPolicyService:
    async def evaluate_assessment(
        self,
        *,
        db: AsyncSession,
        assessment_id: UUID | str,
    ) -> RiskPolicyEvaluation:
        try:
            normalized_id = UUID(
                str(
                    assessment_id
                )
            )

        except ValueError as exc:
            raise RiskPolicyError(
                "Invalid assessment ID."
            ) from exc

        assessment = await db.get(
            Assessment,
            normalized_id,
        )

        if assessment is None:
            raise RiskPolicyError(
                "Assessment not found."
            )

        result = await db.execute(
            select(
                Finding
            )
            .where(
                Finding.assessment_id
                == normalized_id
            )
            .order_by(
                Finding.created_at
            )
        )

        findings = list(
            result.scalars().all()
        )

        if not findings:
            raise RiskPolicyError(
                "Assessment contains "
                "no findings."
            )

        decisions: list[
            PolicyDecision
        ] = []

        for finding in findings:
            finding_type = str(
                finding.extra_data.get(
                    "finding_type",
                    "",
                )
            )

            rule = POLICY_RULES.get(
                (
                    finding_type,
                    finding.category,
                )
            )

            if rule is None:
                proposed_severity = (
                    "unrated"
                )

                rule_id = None

                rationale = (
                    "No matching policy rule. "
                    "Finding remains unrated."
                )

            else:
                proposed_severity = (
                    rule["severity"]
                )

                rule_id = (
                    rule["rule_id"]
                )

                rationale = (
                    rule["rationale"]
                )

            decisions.append(
                PolicyDecision(
                    finding_id=(
                        finding.id
                    ),
                    finding_type=(
                        finding_type
                    ),
                    category=(
                        finding.category
                    ),
                    current_severity=(
                        finding.severity
                    ),
                    proposed_severity=(
                        proposed_severity
                    ),
                    rule_id=rule_id,
                    rationale=rationale,
                )
            )

        rated_decisions = [
            decision
            for decision
            in decisions
            if (
                decision
                .proposed_severity
                != "unrated"
            )
        ]

        proposed_overall_risk = None

        if rated_decisions:
            proposed_overall_risk = max(
                (
                    decision
                    .proposed_severity
                    for decision
                    in rated_decisions
                ),
                key=lambda severity:
                    SEVERITY_RANK[
                        severity
                    ],
            )

        logger.info(
            "Risk policy evaluation "
            "completed. "
            "assessment_id=%s "
            "policy=%s "
            "findings=%s "
            "overall_risk=%s",
            normalized_id,
            POLICY_VERSION,
            len(decisions),
            proposed_overall_risk,
        )

        return RiskPolicyEvaluation(
            assessment_id=(
                normalized_id
            ),
            policy_version=(
                POLICY_VERSION
            ),
            proposed_overall_risk=(
                proposed_overall_risk
            ),
            decisions=decisions,
        )