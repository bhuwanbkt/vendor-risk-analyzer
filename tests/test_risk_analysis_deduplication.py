from __future__ import annotations

from vendor_risk_analyzer.retrieval.service import (
    RetrievalResult,
)
from vendor_risk_analyzer.risk_analysis.schemas import (
    EvidenceAnalysis,
    RiskSignal,
)
from vendor_risk_analyzer.risk_analysis.service import (
    RiskAnalysisService,
)


def service() -> RiskAnalysisService:
    return RiskAnalysisService.__new__(
        RiskAnalysisService
    )


def evidence(
    *,
    chunk_id: str,
    document_id: str,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_id=document_id,
        sequence=1,
        content="Grounded evidence.",
        metadata={},
        cosine_distance=0.2,
        similarity=0.8,
    )


def test_northstar_gap_and_explicit_risk_merge() -> None:
    analysis = EvidenceAnalysis(
        summary="Northstar assessment summary.",
        findings=[
            RiskSignal(
                finding_type="evidence_gap",
                category="business_continuity",
                title=(
                    "Evidence of Completed Full "
                    "2026 Technical Recovery "
                    "Exercise Not Available"
                ),
                description=(
                    "Evidence of a completed full "
                    "technical recovery exercise "
                    "for calendar year 2026 is not "
                    "available because the test is "
                    "scheduled for November 2026."
                ),
                evidence_chunk_ids=[
                    "gap"
                ],
                confidence=1.0,
            ),
            RiskSignal(
                finding_type="explicit_risk",
                category="business_continuity",
                title=(
                    "Unvalidated Recovery Procedures "
                    "Due to Pending Full "
                    "Disaster-Recovery Exercise"
                ),
                description=(
                    "The organization has not yet "
                    "completed a full technical "
                    "disaster-recovery exercise "
                    "during calendar year 2026, so "
                    "current recovery procedures "
                    "have not been fully validated."
                ),
                evidence_chunk_ids=[
                    "risk"
                ],
                confidence=1.0,
            ),
        ],
    )

    result = service().deduplicate_findings(
        analysis,
        evidence=[
            evidence(
                chunk_id="gap",
                document_id="northstar-doc",
            ),
            evidence(
                chunk_id="risk",
                document_id="northstar-doc",
            ),
        ],
    )

    assert len(result.findings) == 1

    finding = result.findings[0]

    assert (
        finding.finding_type
        == "explicit_risk"
    )

    assert set(
        finding.evidence_chunk_ids
    ) == {
        "gap",
        "risk",
    }

    assert (
        result.summary
        != analysis.summary
    )

    assert (
        "1 normalized finding"
        in result.summary
    )

    assert (
        "Unvalidated Recovery Procedures "
        "Due to Pending Full "
        "Disaster-Recovery Exercise"
        in result.summary
    )

    assert (
        "Evidence of Completed Full "
        "2026 Technical Recovery "
        "Exercise Not Available"
        not in result.summary
    )


def test_unrelated_same_category_findings_stay_separate() -> None:
    analysis = EvidenceAnalysis(
        summary="Two separate continuity issues.",
        findings=[
            RiskSignal(
                finding_type="evidence_gap",
                category="business_continuity",
                title="Recovery Test Evidence Missing",
                description=(
                    "Recovery test evidence is "
                    "not available."
                ),
                evidence_chunk_ids=["gap"],
                confidence=0.9,
            ),
            RiskSignal(
                finding_type="explicit_risk",
                category="business_continuity",
                title="Backup Replication Failure Open",
                description=(
                    "A backup replication failure "
                    "remains unresolved."
                ),
                evidence_chunk_ids=["risk"],
                confidence=0.9,
            ),
        ],
    )

    result = service().deduplicate_findings(
        analysis,
        evidence=[
            evidence(
                chunk_id="gap",
                document_id="same-doc",
            ),
            evidence(
                chunk_id="risk",
                document_id="same-doc",
            ),
        ],
    )

    assert len(result.findings) == 2

    assert (
        result.summary
        == analysis.summary
    )


def test_similar_findings_from_different_documents_stay_separate() -> None:
    analysis = EvidenceAnalysis(
        summary="Separate source documents.",
        findings=[
            RiskSignal(
                finding_type="evidence_gap",
                category="business_continuity",
                title=(
                    "Full Recovery Exercise "
                    "Evidence Not Available"
                ),
                description=(
                    "Recovery exercise evidence "
                    "is unavailable."
                ),
                evidence_chunk_ids=["gap"],
                confidence=0.9,
            ),
            RiskSignal(
                finding_type="explicit_risk",
                category="business_continuity",
                title=(
                    "Full Recovery Exercise "
                    "Not Completed"
                ),
                description=(
                    "The recovery exercise has "
                    "not been completed."
                ),
                evidence_chunk_ids=["risk"],
                confidence=0.9,
            ),
        ],
    )

    result = service().deduplicate_findings(
        analysis,
        evidence=[
            evidence(
                chunk_id="gap",
                document_id="doc-a",
            ),
            evidence(
                chunk_id="risk",
                document_id="doc-b",
            ),
        ],
    )

    assert len(result.findings) == 2
