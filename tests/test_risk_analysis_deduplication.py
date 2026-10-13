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


# ------------------------------------------------------------
# Contradiction + explicit risk about the same issue
# ------------------------------------------------------------

PENTEST_RISK = RiskSignal(
    finding_type="explicit_risk",
    category="vulnerability_management",
    title="Unresolved High-Severity Penetration Test Findings Exceed Remediation Timeline",
    description=(
        "Two high-severity findings from the November 2025 penetration test, "
        "an authorization bypass and an outdated web server component, remain "
        "unresolved beyond the 30-day remediation timeline."
    ),
    evidence_chunk_ids=["policy-vuln"],
    confidence=0.95,
)

PENTEST_CONTRADICTION = RiskSignal(
    finding_type="contradiction",
    category="vulnerability_management",
    title="Contradictory Claims Regarding Open Penetration Test Findings",
    description=(
        "The questionnaire states there are no open high or critical "
        "penetration test findings, while the security policy states two "
        "high-severity findings remain unresolved."
    ),
    evidence_chunk_ids=["policy-vuln", "questionnaire-vuln"],
    confidence=0.9,
)


def larkspur_evidence() -> list[RetrievalResult]:
    return [
        evidence(chunk_id="policy-vuln", document_id="policy"),
        evidence(chunk_id="policy-access", document_id="policy"),
        evidence(chunk_id="questionnaire-vuln", document_id="questionnaire"),
        evidence(chunk_id="questionnaire-access", document_id="questionnaire"),
    ]


def test_contradiction_merges_into_explicit_risk_on_same_issue() -> None:
    for order in (
        [PENTEST_RISK, PENTEST_CONTRADICTION],
        [PENTEST_CONTRADICTION, PENTEST_RISK],
    ):
        result = service().deduplicate_findings(
            EvidenceAnalysis(summary="Larkspur summary.", findings=order),
            evidence=larkspur_evidence(),
        )

        assert len(result.findings) == 1
        merged = result.findings[0]
        assert merged.finding_type == "explicit_risk"
        assert merged.title == PENTEST_RISK.title
        assert merged.description.startswith(PENTEST_RISK.description)
        assert "The sources also disagree:" in merged.description
        assert "no open high or critical" in merged.description
        assert merged.evidence_chunk_ids == list(
            dict.fromkeys(
                [*order[0].evidence_chunk_ids, *order[1].evidence_chunk_ids]
            )
        )
        assert merged.confidence == 0.95


def test_contradiction_about_another_control_stays_separate() -> None:
    # MFA risk and the access-review contradiction cite the same policy
    # section chunk, but they are different issues.
    mfa_risk = RiskSignal(
        finding_type="explicit_risk",
        category="access_control",
        title="MFA Not Enforced for 14 Contractor Accounts",
        description=(
            "Multi-factor authentication is not enforced for 14 contractor "
            "accounts that access the customer support console, with no "
            "committed remediation date."
        ),
        evidence_chunk_ids=["policy-access"],
        confidence=0.95,
    )
    review_contradiction = RiskSignal(
        finding_type="contradiction",
        category="access_control",
        title="Inconsistent User Access Review Frequency",
        description=(
            "The policy states user access reviews are performed quarterly, "
            "while the questionnaire states they are performed annually."
        ),
        evidence_chunk_ids=["policy-access", "questionnaire-access"],
        confidence=0.9,
    )

    result = service().deduplicate_findings(
        EvidenceAnalysis(
            summary="Access findings.",
            findings=[mfa_risk, review_contradiction],
        ),
        evidence=larkspur_evidence(),
    )

    assert [f.finding_type for f in result.findings] == [
        "explicit_risk",
        "contradiction",
    ]


def test_contradiction_without_shared_chunk_stays_separate() -> None:
    contradiction = PENTEST_CONTRADICTION.model_copy(
        update={"evidence_chunk_ids": ["questionnaire-vuln"]}
    )

    result = service().deduplicate_findings(
        EvidenceAnalysis(
            summary="Separate findings.",
            findings=[PENTEST_RISK, contradiction],
        ),
        evidence=larkspur_evidence(),
    )

    assert len(result.findings) == 2


def test_contradiction_never_merges_with_evidence_gap() -> None:
    gap = PENTEST_RISK.model_copy(update={"finding_type": "evidence_gap"})

    result = service().deduplicate_findings(
        EvidenceAnalysis(
            summary="Separate findings.",
            findings=[gap, PENTEST_CONTRADICTION],
        ),
        evidence=larkspur_evidence(),
    )

    assert len(result.findings) == 2


def test_merged_description_stays_within_schema_limit() -> None:
    long_risk = PENTEST_RISK.model_copy(
        update={"description": "Open penetration test findings. " * 35}
    )

    result = service().deduplicate_findings(
        EvidenceAnalysis(
            summary="Long description case.",
            findings=[long_risk, PENTEST_CONTRADICTION],
        ),
        evidence=larkspur_evidence(),
    )

    assert len(result.findings) == 1
    assert len(result.findings[0].description) <= 1200
