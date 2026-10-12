from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    field_validator,
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


ModelCoverageStatus = Literal[
    "assessed",
    "insufficient_evidence",
]


# Final coverage states stored on an assessment:
# - assessed: the evidence addresses the category
# - insufficient_evidence: evidence was retrieved but does not
#   meaningfully address the category
# - no_evidence: retrieval returned nothing for the category
# - not_reported: the model omitted the category
CoverageStatus = Literal[
    "assessed",
    "insufficient_evidence",
    "no_evidence",
    "not_reported",
]


COVERAGE_NOTE_MAX_LENGTH = 300


class CategoryCoverage(BaseModel):
    """Model-reported coverage for one risk category."""

    category: RiskCategory
    status: ModelCoverageStatus
    note: str = Field(
        default="",
        max_length=COVERAGE_NOTE_MAX_LENGTH,
    )

    @field_validator("note", mode="before")
    @classmethod
    def truncate_note(cls, value):
        # An overlong note must not fail the whole assessment.
        if isinstance(value, str):
            return value.strip()[:COVERAGE_NOTE_MAX_LENGTH]
        return value


class CategoryCoverageResult(BaseModel):
    """Final per-category coverage persisted with an assessment."""

    category: RiskCategory
    status: CoverageStatus
    evidence_chunks: int = Field(ge=0)
    top_similarity: float | None = None
    finding_count: int = Field(ge=0)
    note: str = ""


class EvidenceAnalysis(BaseModel):
    summary: str = Field(
        min_length=10,
        max_length=1200,
    )

    findings: list[RiskSignal]
    category_coverage: list[CategoryCoverage] = Field(
        default_factory=list,
    )


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
    coverage: list[CategoryCoverageResult] = Field(
        default_factory=list,
    )
    # Model findings removed because no valid evidence remained.
    dropped_finding_count: int = Field(default=0, ge=0)
