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
    "vendor-risk-policy-v3"
)


SEVERITY_RANK = {
    "unrated": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


# ------------------------------------------------------------
# Severity matrix: category -> finding type -> severity.
#
# Rules never assign "critical"; a critical rating requires
# analyst judgement beyond what a document-only rule can show.
#
#   explicit_risk  - the source itself describes an open risk
#   contradiction  - sources disagree about the same control
#   evidence_gap   - the source says required evidence is missing
# ------------------------------------------------------------

SEVERITY_MATRIX = {
    "access_control": {
        "explicit_risk": "high",
        "contradiction": "medium",
        "evidence_gap": "medium",
    },
    "encryption": {
        "explicit_risk": "high",
        "contradiction": "medium",
        "evidence_gap": "medium",
    },
    "incident_response": {
        "explicit_risk": "high",
        "contradiction": "medium",
        "evidence_gap": "medium",
    },
    "vulnerability_management": {
        "explicit_risk": "high",
        "contradiction": "medium",
        "evidence_gap": "medium",
    },
    "privacy": {
        "explicit_risk": "high",
        "contradiction": "medium",
        "evidence_gap": "medium",
    },
    "business_continuity": {
        "explicit_risk": "medium",
        "contradiction": "medium",
        "evidence_gap": "medium",
    },
    "data_retention": {
        "explicit_risk": "medium",
        "contradiction": "medium",
        "evidence_gap": "low",
    },
    "third_party": {
        "explicit_risk": "medium",
        "contradiction": "medium",
        "evidence_gap": "low",
    },
    "ai_governance": {
        "explicit_risk": "medium",
        "contradiction": "medium",
        "evidence_gap": "low",
    },
    "other": {
        "explicit_risk": "low",
        "contradiction": "low",
        "evidence_gap": "low",
    },
}


CATEGORY_CODES = {
    "access_control": "AC",
    "encryption": "ENC",
    "incident_response": "IR",
    "vulnerability_management": "VM",
    "privacy": "PRV",
    "business_continuity": "BC",
    "data_retention": "RET",
    "third_party": "TP",
    "ai_governance": "AIG",
    "other": "OTH",
}


TYPE_CODES = {
    "explicit_risk": "EXPLICIT",
    "contradiction": "CONTRADICTION",
    "evidence_gap": "GAP",
}


RATIONALE_TEMPLATES = {
    "explicit_risk": (
        "The vendor's documents describe an open "
        "{label} risk or issue that requires "
        "remediation tracking."
    ),
    "contradiction": (
        "The vendor's documents make conflicting "
        "{label} statements, so the actual control "
        "requires analyst review."
    ),
    "evidence_gap": (
        "The vendor's documents state that required "
        "{label} evidence is missing or incomplete."
    ),
}


# Original v1 rules keep their exact rationale text.
RATIONALE_OVERRIDES = {
    (
        "contradiction",
        "incident_response",
    ): (
        "Conflicting incident-response "
        "requirements require analyst "
        "review because they may create "
        "operational ambiguity."
    ),
    (
        "explicit_risk",
        "business_continuity",
    ): (
        "An explicitly documented open "
        "business-continuity issue "
        "requires remediation tracking."
    ),
}


def _build_policy_rules() -> dict[tuple[str, str], dict[str, str]]:
    rules = {}

    for category, severities in SEVERITY_MATRIX.items():
        label = category.replace("_", " ")

        for finding_type, severity in severities.items():
            key = (finding_type, category)

            rules[key] = {
                "severity": severity,
                "rule_id": (
                    f"VRP-{CATEGORY_CODES[category]}-"
                    f"{TYPE_CODES[finding_type]}-001"
                ),
                "rationale": RATIONALE_OVERRIDES.get(
                    key,
                    RATIONALE_TEMPLATES[finding_type].format(
                        label=label
                    ),
                ),
            }

    return rules


POLICY_RULES = _build_policy_rules()


# ------------------------------------------------------------
# Numeric risk score (policy v3).
#
# Each rated finding contributes a weight by severity. Weights
# combine with diminishing returns, like independent
# probabilities, so the score rises with every finding but can
# never exceed 100:
#
#     score = 100 x (1 - product(1 - weight))
#
# One high finding scores 30; two high findings 51; no findings
# 0. Unrated findings contribute nothing. The score describes
# documented findings only; read it with coverage_percent.
# ------------------------------------------------------------

SCORE_METHOD = "weighted-findings-v1"

SEVERITY_WEIGHTS = {
    "unrated": 0.0,
    "low": 0.05,
    "medium": 0.15,
    "high": 0.30,
    "critical": 0.50,
}


def compute_risk_score(
    severities,
) -> float:
    remaining = 1.0

    for severity in severities:
        remaining *= 1.0 - SEVERITY_WEIGHTS.get(
            severity,
            0.0,
        )

    return round(
        100.0 * (1.0 - remaining),
        1,
    )


def compute_coverage_percent(
    coverage,
) -> float | None:
    """Percent of searched categories marked assessed."""
    if not isinstance(coverage, list) or not coverage:
        return None

    statuses = [
        item.get("status")
        for item in coverage
        if isinstance(item, dict)
    ]

    if not statuses:
        return None

    assessed = sum(
        1
        for status in statuses
        if status == "assessed"
    )

    return round(
        100.0 * assessed / len(statuses),
        1,
    )


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
            proposed_overall_score=compute_risk_score(
                decision.proposed_severity
                for decision in decisions
            ),
            coverage_percent=compute_coverage_percent(
                (assessment.extra_data or {}).get(
                    "category_coverage"
                )
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

        - assessments.overall_score (policy v3)
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

            assessment.overall_score = (
                evaluation
                .proposed_overall_score
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
                "score_method":
                    SCORE_METHOD,
                "coverage_percent":
                    (
                        evaluation
                        .coverage_percent
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