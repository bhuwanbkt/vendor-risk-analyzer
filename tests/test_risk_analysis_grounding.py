from __future__ import annotations

import pytest

from vendor_risk_analyzer.retrieval.service import (
    RetrievalResult,
)
from vendor_risk_analyzer.risk_analysis.schemas import (
    EvidenceAnalysis,
    RiskSignal,
)
from vendor_risk_analyzer.risk_analysis.service import (
    RiskAnalysisGroundingError,
    RiskAnalysisService,
)


def service() -> RiskAnalysisService:
    return RiskAnalysisService.__new__(
        RiskAnalysisService
    )


def evidence(
    chunk_id: str,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_id="doc-1",
        sequence=1,
        content="Evidence text.",
        metadata={},
        cosine_distance=0.2,
        similarity=0.8,
    )


def analysis_with(
    signal: RiskSignal,
) -> EvidenceAnalysis:
    return EvidenceAnalysis(
        summary="Grounding validation summary.",
        findings=[signal],
    )


def test_valid_contradiction_passes() -> None:
    analysis = analysis_with(
        RiskSignal(
            finding_type="contradiction",
            category="incident_response",
            title="Conflicting notification timelines",
            description=(
                "Two retrieved sources contain "
                "different notification timelines."
            ),
            evidence_chunk_ids=[
                "chunk-a",
                "chunk-b",
            ],
            confidence=1.0,
        )
    )

    service().validate_analysis(
        analysis=analysis,
        evidence=[
            evidence("chunk-a"),
            evidence("chunk-b"),
        ],
    )


def test_contradiction_requires_two_chunks() -> None:
    analysis = analysis_with(
        RiskSignal(
            finding_type="contradiction",
            category="incident_response",
            title="Conflicting notification timeline",
            description=(
                "Only one source was cited for "
                "a contradiction."
            ),
            evidence_chunk_ids=[
                "chunk-a"
            ],
            confidence=1.0,
        )
    )

    with pytest.raises(
        RiskAnalysisGroundingError,
        match="at least two",
    ):
        service().validate_analysis(
            analysis=analysis,
            evidence=[
                evidence("chunk-a")
            ],
        )


def test_unretrieved_chunk_is_rejected() -> None:
    analysis = analysis_with(
        RiskSignal(
            finding_type="explicit_risk",
            category="business_continuity",
            title="Open recovery exercise risk",
            description=(
                "The source describes an open "
                "recovery exercise risk."
            ),
            evidence_chunk_ids=[
                "missing-chunk"
            ],
            confidence=0.9,
        )
    )

    with pytest.raises(
        RiskAnalysisGroundingError,
        match="unretrieved evidence",
    ):
        service().validate_analysis(
            analysis=analysis,
            evidence=[
                evidence("chunk-a")
            ],
        )


def test_duplicate_chunk_ids_are_rejected() -> None:
    analysis = analysis_with(
        RiskSignal(
            finding_type="explicit_risk",
            category="business_continuity",
            title="Open recovery exercise risk",
            description=(
                "The source describes an open "
                "recovery exercise risk."
            ),
            evidence_chunk_ids=[
                "chunk-a",
                "chunk-a",
            ],
            confidence=0.9,
        )
    )

    with pytest.raises(
        RiskAnalysisGroundingError,
        match="duplicate evidence",
    ):
        service().validate_analysis(
            analysis=analysis,
            evidence=[
                evidence("chunk-a")
            ],
        )
