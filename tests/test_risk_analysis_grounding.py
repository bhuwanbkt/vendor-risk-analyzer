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


def signal(finding_type, chunk_ids, title="Grounding test finding"):
    return RiskSignal(
        finding_type=finding_type,
        category="incident_response",
        title=title,
        description="A finding used for grounding repair tests.",
        evidence_chunk_ids=chunk_ids,
        confidence=0.8,
    )


def test_repair_keeps_valid_findings_and_drops_ungrounded_ones() -> None:
    analysis = EvidenceAnalysis(
        summary="Original model summary.",
        findings=[
            signal("explicit_risk", ["chunk-a"], "Valid finding kept"),
            signal("explicit_risk", ["invented"], "Invented citation"),
            signal("contradiction", ["chunk-a", "invented"], "Weak contradiction"),
            signal("evidence_gap", ["chunk-a", "chunk-a", "chunk-b"], "Duplicate IDs"),
        ],
    )
    available = [evidence("chunk-a"), evidence("chunk-b")]

    repaired, dropped = service().repair_grounding(
        analysis=analysis,
        evidence=available,
    )

    assert dropped == 2
    assert [finding.title for finding in repaired.findings] == [
        "Valid finding kept",
        "Duplicate IDs",
    ]
    assert repaired.findings[1].evidence_chunk_ids == ["chunk-a", "chunk-b"]
    # The repaired analysis passes the strict validator.
    service().validate_analysis(analysis=repaired, evidence=available)


def test_repair_removes_invented_ids_but_keeps_grounded_finding() -> None:
    analysis = EvidenceAnalysis(
        summary="Original model summary.",
        findings=[signal("explicit_risk", ["invented", "chunk-b"])],
    )

    repaired, dropped = service().repair_grounding(
        analysis=analysis,
        evidence=[evidence("chunk-b")],
    )

    assert dropped == 0
    assert repaired.findings[0].evidence_chunk_ids == ["chunk-b"]


def test_dropped_findings_rebuild_the_summary(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    analyzer = service()
    analyzer.model = "test-model"

    async def collect_evidence(**kwargs):
        return [evidence("chunk-a")], {"chunk-a": {"incident_response"}}

    async def run_llm(**kwargs):
        return EvidenceAnalysis(
            summary="Mentions an invented finding that will be dropped.",
            findings=[
                signal("explicit_risk", ["chunk-a"], "Grounded finding"),
                signal("explicit_risk", ["invented"], "Invented finding"),
            ],
        )

    async def commit():
        return None

    analyzer.collect_evidence = collect_evidence
    analyzer._run_llm = run_llm

    result = asyncio.run(
        analyzer.analyze_vendor(
            db=SimpleNamespace(commit=commit), vendor_id="vendor-1"
        )
    )

    assert result.dropped_finding_count == 1
    assert [finding.title for finding in result.findings] == ["Grounded finding"]
    assert "Invented" not in result.summary
    assert "Grounded finding" in result.summary
