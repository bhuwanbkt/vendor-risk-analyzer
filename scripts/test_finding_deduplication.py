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


def evidence(
    *,
    chunk_id: str,
    document_id: str,
    content: str,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_id=document_id,
        sequence=1,
        content=content,
        metadata={},
        cosine_distance=0.2,
        similarity=0.8,
    )


def signal(
    *,
    finding_type: str,
    category: str,
    title: str,
    description: str,
    chunk_ids: list[str],
    confidence: float = 1.0,
) -> RiskSignal:
    return RiskSignal(
        finding_type=finding_type,
        category=category,
        title=title,
        description=description,
        evidence_chunk_ids=chunk_ids,
        confidence=confidence,
    )


def service() -> RiskAnalysisService:
    # Deduplication does not need the
    # retriever, API key, or model client.
    return RiskAnalysisService.__new__(
        RiskAnalysisService
    )


def test_northstar_gap_and_risk_merge() -> None:
    analysis = EvidenceAnalysis(
        summary=(
            "Northstar assessment summary."
        ),
        findings=[
            signal(
                finding_type=(
                    "contradiction"
                ),
                category=(
                    "incident_response"
                ),
                title=(
                    "Contradictory Incident "
                    "Notification Timeframes"
                ),
                description=(
                    "One source says 48 hours "
                    "and another says 72 hours."
                ),
                chunk_ids=[
                    "incident-48",
                    "incident-72",
                ],
            ),
            signal(
                finding_type=(
                    "evidence_gap"
                ),
                category=(
                    "business_continuity"
                ),
                title=(
                    "Evidence of Completed Full "
                    "2026 Technical Recovery "
                    "Exercise Not Available"
                ),
                description=(
                    "The evidence explicitly states "
                    "that at the date of the document, "
                    "evidence of a completed full "
                    "technical recovery exercise for "
                    "calendar year 2026 is not "
                    "available as the test is "
                    "scheduled for November 2026."
                ),
                chunk_ids=[
                    "dr-gap",
                ],
            ),
            signal(
                finding_type=(
                    "explicit_risk"
                ),
                category=(
                    "business_continuity"
                ),
                title=(
                    "Unvalidated Recovery Procedures "
                    "Due to Pending Full "
                    "Disaster-Recovery Exercise"
                ),
                description=(
                    "An open risk (Risk ID: "
                    "NR-BC-2026-01) notes that the "
                    "organization has not yet "
                    "completed a full technical "
                    "disaster-recovery exercise "
                    "during calendar year 2026, "
                    "meaning current recovery "
                    "procedures have not been fully "
                    "validated against the documented "
                    "RTO of 8 hours and RPO of "
                    "4 hours."
                ),
                chunk_ids=[
                    "dr-risk",
                ],
            ),
        ],
    )

    evidence_items = [
        evidence(
            chunk_id="incident-48",
            document_id="northstar-doc",
            content="48 hour notice.",
        ),
        evidence(
            chunk_id="incident-72",
            document_id="northstar-doc",
            content="72 hour notice.",
        ),
        evidence(
            chunk_id="dr-gap",
            document_id="northstar-doc",
            content=(
                "Exercise evidence is not "
                "available."
            ),
        ),
        evidence(
            chunk_id="dr-risk",
            document_id="northstar-doc",
            content=(
                "Full DR exercise has not "
                "been completed."
            ),
        ),
    ]

    result = service().deduplicate_findings(
        analysis,
        evidence=evidence_items,
    )

    assert len(result.findings) == 2

    contradiction = next(
        item
        for item in result.findings
        if item.finding_type
        == "contradiction"
    )

    assert set(
        contradiction.evidence_chunk_ids
    ) == {
        "incident-48",
        "incident-72",
    }

    continuity = next(
        item
        for item in result.findings
        if item.category
        == "business_continuity"
    )

    assert (
        continuity.finding_type
        == "explicit_risk"
    )

    assert set(
        continuity.evidence_chunk_ids
    ) == {
        "dr-gap",
        "dr-risk",
    }

    assert (
        result.summary
        != analysis.summary
    )

    assert (
        "2 normalized findings"
        in result.summary
    )

    assert (
        "Evidence of Completed Full 2026 "
        "Technical Recovery Exercise Not "
        "Available"
        not in result.summary
    )

    assert (
        "Unvalidated Recovery Procedures "
        "Due to Pending Full "
        "Disaster-Recovery Exercise"
        in result.summary
    )


def test_unrelated_same_category_stays_separate() -> None:
    analysis = EvidenceAnalysis(
        summary="Separate findings summary.",
        findings=[
            signal(
                finding_type="evidence_gap",
                category="business_continuity",
                title=(
                    "Recovery Exercise Evidence "
                    "Not Available"
                ),
                description=(
                    "Recovery exercise evidence "
                    "is unavailable."
                ),
                chunk_ids=["gap"],
            ),
            signal(
                finding_type="explicit_risk",
                category="business_continuity",
                title=(
                    "Backup Replication Failure "
                    "Remains Open"
                ),
                description=(
                    "A backup replication issue "
                    "remains unresolved."
                ),
                chunk_ids=["risk"],
            ),
        ],
    )

    evidence_items = [
        evidence(
            chunk_id="gap",
            document_id="same-doc",
            content="Missing exercise evidence.",
        ),
        evidence(
            chunk_id="risk",
            document_id="same-doc",
            content="Backup issue remains open.",
        ),
    ]

    result = service().deduplicate_findings(
        analysis,
        evidence=evidence_items,
    )

    assert len(result.findings) == 2

    assert (
        result.summary
        == analysis.summary
    )


def test_gap_and_risk_from_different_documents_stay_separate() -> None:
    analysis = EvidenceAnalysis(
        summary="Separate document summary.",
        findings=[
            signal(
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
                chunk_ids=["gap"],
            ),
            signal(
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
                chunk_ids=["risk"],
            ),
        ],
    )

    evidence_items = [
        evidence(
            chunk_id="gap",
            document_id="doc-a",
            content="Missing exercise evidence.",
        ),
        evidence(
            chunk_id="risk",
            document_id="doc-b",
            content="Exercise remains open.",
        ),
    ]

    result = service().deduplicate_findings(
        analysis,
        evidence=evidence_items,
    )

    assert len(result.findings) == 2


def test_exact_duplicate_same_type_merges() -> None:
    analysis = EvidenceAnalysis(
        summary="Duplicate summary.",
        findings=[
            signal(
                finding_type="explicit_risk",
                category="business_continuity",
                title="Open DR Exercise Risk",
                description=(
                    "The DR exercise remains open."
                ),
                chunk_ids=["dr"],
                confidence=0.8,
            ),
            signal(
                finding_type="explicit_risk",
                category="business_continuity",
                title="Open DR Risk",
                description=(
                    "The DR exercise remains open."
                ),
                chunk_ids=["dr"],
                confidence=0.9,
            ),
        ],
    )

    evidence_items = [
        evidence(
            chunk_id="dr",
            document_id="doc",
            content="The DR exercise remains open.",
        ),
    ]

    result = service().deduplicate_findings(
        analysis,
        evidence=evidence_items,
    )

    assert len(result.findings) == 1
    assert result.findings[0].confidence == 0.9

    assert (
        "1 normalized finding"
        in result.summary
    )


def main() -> int:
    tests = [
        test_northstar_gap_and_risk_merge,
        test_unrelated_same_category_stays_separate,
        (
            test_gap_and_risk_from_different_documents_stay_separate
        ),
        test_exact_duplicate_same_type_merges,
    ]

    for test in tests:
        test()

        print(
            f"PASS: {test.__name__}"
        )

    print()
    print(
        "PASS: finding deduplication "
        "regression suite completed."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
