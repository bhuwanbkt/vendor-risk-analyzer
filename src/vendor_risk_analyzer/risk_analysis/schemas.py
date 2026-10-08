from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    Field,
)


FindingType = Literal[
    "contradiction",
    "evidence_gap",
    "explicit_risk",
]


RiskCategory = Literal[
    "access_control",
    "encryption",
    "incident_response",
    "data_retention",
    "vulnerability_management",
    "business_continuity",
    "third_party",
    "privacy",
    "ai_governance",
    "other",
]


class RiskSignal(BaseModel):
    finding_type: FindingType

    category: RiskCategory

    title: str = Field(
        min_length=5,
        max_length=200,
    )

    description: str = Field(
        min_length=10,
        max_length=1200,
    )

    evidence_chunk_ids: list[str] = Field(
        min_length=1,
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )


class EvidenceAnalysis(BaseModel):
    summary: str = Field(
        min_length=10,
        max_length=1200,
    )

    findings: list[RiskSignal]


class RiskEvidence(BaseModel):
    chunk_id: str

    document_id: str

    sequence: int

    similarity: float

    retrieval_topics: list[str]


class VendorRiskAnalysisResult(
    BaseModel
):
    vendor_id: str

    model: str

    summary: str

    findings: list[RiskSignal]

    evidence: list[RiskEvidence]

    raw_finding_count: int

    normalized_finding_count: int