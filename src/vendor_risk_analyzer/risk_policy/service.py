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
        """
        Evaluate an existing assessment
        against deterministic policy rules.

        This method is READ ONLY.

        It does not modify:
        - finding severity
        - finding rule_id
        - assessment overall_risk
        - assessment overall_score
        """

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
                    rule_id=(
                        rule_id
                    ),
                    rationale=(
                        rationale
                    ),
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
            len(
                decisions
            ),
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
            decisions=(
                decisions
            ),
        )

    async def apply_assessment(
        self,
        *,
        db: AsyncSession,
        assessment_id: UUID | str,
    ) -> RiskPolicyEvaluation:
        """
        Evaluate the assessment and persist
        deterministic policy decisions.

        Updates:
        - findings.severity
        - findings.rule_id
        - findings.metadata
        - assessments.overall_risk
        - assessments.metadata

        Does NOT calculate or modify
        overall_score.
        """

        evaluation = (
            await self.evaluate_assessment(
                db=db,
                assessment_id=assessment_id,
            )
        )

        assessment = await db.get(
            Assessment,
            evaluation.assessment_id,
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
                == evaluation.assessment_id
            )
        )

        findings = {
            finding.id:
                finding
            for finding
            in result.scalars().all()
        }

        if (
            len(findings)
            != len(
                evaluation.decisions
            )
        ):
            raise RiskPolicyError(
                "Finding count changed "
                "between evaluation and "
                "policy application."
            )

        try:
            for decision in (
                evaluation.decisions
            ):
                finding = findings.get(
                    decision.finding_id
                )

                if finding is None:
                    raise RiskPolicyError(
                        "Finding disappeared "
                        "during policy "
                        "application."
                    )

                finding.severity = (
                    decision
                    .proposed_severity
                )

                finding.rule_id = (
                    decision.rule_id
                )

                finding.extra_data = {
                    **finding.extra_data,
                    "severity_policy":
                        (
                            evaluation
                            .policy_version
                        ),
                    "policy_rule_id":
                        (
                            decision.rule_id
                        ),
                    "policy_rationale":
                        (
                            decision.rationale
                        ),
                    "policy_applied":
                        True,
                }

            assessment.overall_risk = (
                evaluation
                .proposed_overall_risk
            )

            rated_finding_count = sum(
                1
                for decision
                in evaluation.decisions
                if (
                    decision
                    .proposed_severity
                    != "unrated"
                )
            )

            assessment.extra_data = {
                **assessment.extra_data,
                "risk_policy_version":
                    (
                        evaluation
                        .policy_version
                    ),
                "policy_applied":
                    True,
                "rated_finding_count":
                    (
                        rated_finding_count
                    ),
                "total_finding_count":
                    len(
                        evaluation.decisions
                    ),
            }

            await db.commit()

        except Exception:
            await db.rollback()

            logger.exception(
                "Risk policy application "
                "failed. "
                "assessment_id=%s",
                evaluation.assessment_id,
            )

            raise

        logger.info(
            "Risk policy applied. "
            "assessment_id=%s "
            "policy=%s "
            "overall_risk=%s "
            "findings=%s",
            evaluation.assessment_id,
            evaluation.policy_version,
            evaluation.proposed_overall_risk,
            len(
                evaluation.decisions
            ),
        )

        return evaluation