"""Runtime safeguards around the risk-analysis model call."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from vendor_risk_analyzer.retrieval.service import RetrievalResult
from vendor_risk_analyzer.risk_analysis.schemas import EvidenceAnalysis
from vendor_risk_analyzer.risk_analysis.service import (
    RiskAnalysisError,
    RiskAnalysisService,
)


pytestmark = pytest.mark.unit


def service() -> RiskAnalysisService:
    instance = RiskAnalysisService.__new__(RiskAnalysisService)
    instance.model = "test-model"
    return instance


def evidence() -> RetrievalResult:
    return RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        sequence=1,
        content="Evidence text.",
        metadata={},
        cosine_distance=0.2,
        similarity=0.8,
    )


def test_database_transaction_ends_before_model_call():
    events = []
    analyzer = service()

    async def collect_evidence(**kwargs):
        events.append("retrieval")
        return [evidence()], {"chunk-1": {"incident_response"}}

    async def run_llm(**kwargs):
        events.append("llm")
        return EvidenceAnalysis(summary="No qualifying findings.", findings=[])

    async def commit():
        events.append("commit")

    analyzer.collect_evidence = collect_evidence
    analyzer._run_llm = run_llm

    result = asyncio.run(
        analyzer.analyze_vendor(
            db=SimpleNamespace(commit=commit), vendor_id="vendor-1"
        )
    )

    assert events == ["retrieval", "commit", "llm"]
    assert result.findings == []


def test_model_call_times_out():
    analyzer = service()
    analyzer.llm_timeout_seconds = 0.05
    analyzer.client = SimpleNamespace(
        interactions=SimpleNamespace(create=lambda **kwargs: time.sleep(0.5))
    )

    with pytest.raises(RiskAnalysisError, match="timed out"):
        asyncio.run(analyzer._run_llm(prompt="prompt"))
