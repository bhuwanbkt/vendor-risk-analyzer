"""Assessment coverage across every risk category."""

from __future__ import annotations

from typing import get_args

import pytest

from vendor_risk_analyzer.retrieval.service import RetrievalResult
from vendor_risk_analyzer.risk_analysis.schemas import (
    CategoryCoverage,
    EvidenceAnalysis,
    RiskCategory,
    RiskSignal,
)
from vendor_risk_analyzer.risk_analysis.service import (
    ASSESSED_CATEGORIES,
    RETRIEVAL_QUERIES,
    RiskAnalysisService,
)


pytestmark = pytest.mark.unit


def service() -> RiskAnalysisService:
    return RiskAnalysisService.__new__(RiskAnalysisService)


def evidence(chunk_id: str, similarity: float = 0.8) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_id="doc-1",
        sequence=1,
        content="Evidence text.",
        metadata={},
        cosine_distance=1 - similarity,
        similarity=similarity,
    )


def signal(category: str, chunk_id: str) -> RiskSignal:
    return RiskSignal(
        finding_type="explicit_risk",
        category=category,
        title="Open access issue",
        description="The document describes an open issue.",
        evidence_chunk_ids=[chunk_id],
        confidence=0.9,
    )


def test_every_category_except_other_has_a_retrieval_query():
    expected = set(get_args(RiskCategory)) - {"other"}

    assert set(ASSESSED_CATEGORIES) == expected
    assert all(query.strip() for _, query in RETRIEVAL_QUERIES)


def test_prompt_asks_for_coverage_of_every_category():
    prompt = service().build_prompt(
        evidence=[evidence("chunk-1")],
        evidence_topics={"chunk-1": {"encryption"}},
    )

    assert "CATEGORY COVERAGE" in prompt
    for category in ASSESSED_CATEGORIES:
        assert category in prompt


def test_coverage_combines_retrieval_findings_and_model_status():
    analysis = EvidenceAnalysis(
        summary="Evidence reviewed for several categories.",
        findings=[signal("access_control", "chunk-ac")],
        category_coverage=[
            CategoryCoverage(
                category="access_control",
                status="insufficient_evidence",
                note="Model said insufficient.",
            ),
            CategoryCoverage(
                category="encryption",
                status="assessed",
                note="TLS 1.2 and AES-256 are described.",
            ),
            CategoryCoverage(
                category="privacy",
                status="insufficient_evidence",
                note="Only a privacy contact is listed.",
            ),
            CategoryCoverage(
                category="data_retention",
                status="assessed",
                note="Model claimed coverage without evidence.",
            ),
        ],
    )

    coverage = RiskAnalysisService.build_coverage(
        analysis=analysis,
        evidence=[
            evidence("chunk-ac", 0.7),
            evidence("chunk-enc", 0.9),
            evidence("chunk-prv", 0.6),
            evidence("chunk-tp", 0.5),
        ],
        evidence_topics={
            "chunk-ac": {"access_control"},
            "chunk-enc": {"encryption", "privacy"},
            "chunk-prv": {"privacy"},
            "chunk-tp": {"third_party"},
        },
    )
    by_category = {item.category: item for item in coverage}

    assert [item.category for item in coverage] == list(ASSESSED_CATEGORIES)

    # A finding means the category was assessed, whatever the model said.
    assert by_category["access_control"].status == "assessed"
    assert by_category["access_control"].finding_count == 1

    assert by_category["encryption"].status == "assessed"
    assert by_category["encryption"].top_similarity == 0.9

    assert by_category["privacy"].status == "insufficient_evidence"
    assert by_category["privacy"].evidence_chunks == 2

    # Nothing retrieved overrides the model's claim.
    assert by_category["data_retention"].status == "no_evidence"
    assert by_category["data_retention"].evidence_chunks == 0
    assert by_category["data_retention"].top_similarity is None

    # Retrieved but omitted by the model.
    assert by_category["third_party"].status == "not_reported"


def test_overlong_model_note_is_truncated_not_rejected():
    item = CategoryCoverage(
        category="encryption",
        status="assessed",
        note="x" * 1000,
    )

    assert len(item.note) == 300


def test_deduplication_keeps_model_coverage():
    analysis = EvidenceAnalysis(
        summary="Evidence reviewed for several categories.",
        findings=[
            signal("access_control", "chunk-1"),
            signal("access_control", "chunk-1"),
        ],
        category_coverage=[
            CategoryCoverage(category="encryption", status="assessed"),
        ],
    )

    normalized = service().deduplicate_findings(
        analysis,
        evidence=[evidence("chunk-1")],
    )

    assert len(normalized.findings) == 1
    assert [item.category for item in normalized.category_coverage] == [
        "encryption"
    ]


def test_prompt_flags_risky_practices_stated_as_fact():
    prompt = service().build_prompt(
        evidence=[evidence("chunk-1")],
        evidence_topics={"chunk-1": {"ai_governance"}},
    )

    assert "RISKY PRACTICES STATED AS FACT" in prompt
    assert "unless the customer opts out" in prompt
    # Guardrails against false positives on normal controls.
    assert "Do not report optional features" in prompt
