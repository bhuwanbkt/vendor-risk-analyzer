from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel


RiskSeverity = Literal[
    "low",
    "medium",
    "high",
    "critical",
    "unrated",
]


class PolicyDecision(BaseModel):
    finding_id: UUID

    finding_type: str

    category: str

    current_severity: str

    proposed_severity: RiskSeverity

    rule_id: str | None

    rationale: str


class RiskPolicyEvaluation(BaseModel):
    assessment_id: UUID

    policy_version: str

    proposed_overall_risk: (
        RiskSeverity | None
    )
    # 0-100, higher means riskier. See compute_risk_score.
    proposed_overall_score: float | None = None
    # Share of searched categories whose evidence was assessed;
    # None for assessments saved before coverage existed.
    coverage_percent: float | None = None

    decisions: list[
        PolicyDecision
    ]