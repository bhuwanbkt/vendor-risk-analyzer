"""Gemini token usage: parsing, accumulation, recording, and summaries."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from vendor_risk_analyzer.risk_analysis.service import (
    RiskAnalysisError,
    RiskAnalysisService,
)
from vendor_risk_analyzer.usage import service as usage_service
from vendor_risk_analyzer.usage.tokens import TokenUsage


pytestmark = pytest.mark.unit


def test_interaction_usage_is_parsed() -> None:
    interaction = SimpleNamespace(
        usage=SimpleNamespace(
            total_input_tokens=1200,
            total_output_tokens=300,
            total_thought_tokens=50,
            total_tokens=1550,
        )
    )

    assert TokenUsage.from_interaction(interaction) == TokenUsage(
        input_tokens=1200,
        output_tokens=300,
        thought_tokens=50,
        total_tokens=1550,
        requests=1,
    )


def test_generate_content_usage_is_parsed_and_total_derived() -> None:
    body = {
        "usageMetadata": {
            "promptTokenCount": 800,
            "candidatesTokenCount": 120,
            "thoughtsTokenCount": 30,
        }
    }

    usage = TokenUsage.from_generate_content(body)

    assert usage.total_tokens == 950
    assert usage.billable_output_tokens == 150
    assert usage.requests == 1


@pytest.mark.parametrize("response", [SimpleNamespace(), SimpleNamespace(usage=None)])
def test_missing_usage_still_counts_the_request(response) -> None:
    assert TokenUsage.from_interaction(response) == TokenUsage(requests=1)
    assert TokenUsage.from_generate_content({}) == TokenUsage(requests=1)


def test_usage_adds_up() -> None:
    first = TokenUsage(10, 5, 1, 16, 1)
    second = TokenUsage(20, 7, 0, 27, 1)

    assert first + second == TokenUsage(30, 12, 1, 43, 2)


def test_cost_estimate_needs_both_prices(monkeypatch) -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=400_000, thought_tokens=100_000)
    monkeypatch.delenv("LLM_INPUT_PRICE_PER_MILLION_TOKENS", raising=False)
    monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MILLION_TOKENS", "0.40")

    assert usage_service.configured_prices() is None
    assert usage_service.estimate_cost(usage, None) is None

    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MILLION_TOKENS", "0.10")
    prices = usage_service.configured_prices()

    # Thinking tokens are billed as output.
    assert usage_service.estimate_cost(usage, prices) == pytest.approx(0.10 + 0.20)


def test_negative_price_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MILLION_TOKENS", "-1")
    monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MILLION_TOKENS", "1")

    with pytest.raises(ValueError, match="cannot be negative"):
        usage_service.configured_prices()


def analyzer(output_text: str | None) -> RiskAnalysisService:
    instance = RiskAnalysisService.__new__(RiskAnalysisService)
    instance.model = "test-model"
    instance.llm_timeout_seconds = 5
    response = SimpleNamespace(
        output_text=output_text,
        usage=SimpleNamespace(
            total_input_tokens=1000,
            total_output_tokens=200,
            total_thought_tokens=0,
            total_tokens=1200,
        ),
    )
    instance.client = SimpleNamespace(
        interactions=SimpleNamespace(create=lambda **kwargs: response)
    )
    return instance


def test_risk_analysis_counts_tokens_even_when_output_is_rejected() -> None:
    service = analyzer("not json")

    with pytest.raises(RiskAnalysisError, match="validation failed"):
        asyncio.run(service._run_llm(prompt="prompt"))

    assert service.token_usage == TokenUsage(1000, 200, 0, 1200, 1)


def test_risk_analysis_usage_accumulates_across_calls() -> None:
    service = analyzer('{"summary": "No qualifying findings.", "findings": []}')

    asyncio.run(service._run_llm(prompt="one"))
    asyncio.run(service._run_llm(prompt="two"))

    assert service.token_usage.requests == 2
    assert service.token_usage.input_tokens == 2000


class Collector:
    def __init__(self, sink, fail=False):
        self.sink = sink
        self.fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, value):
        if self.fail:
            raise RuntimeError("database down")
        self.sink.append(value)

    async def commit(self):
        return None


def test_record_usage_writes_one_event() -> None:
    sink = []
    tenant_id, vendor_id = uuid4(), uuid4()

    asyncio.run(
        usage_service.record_usage(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            feature="chat",
            model="gemini-test",
            usage=TokenUsage(100, 20, 5, 125, 2),
            reference_id="conversation-1",
            actor_subject="user-1",
            factory=lambda: Collector(sink),
        )
    )

    (event,) = sink
    assert (event.tenant_id, event.vendor_id) == (tenant_id, vendor_id)
    assert (event.feature, event.model, event.requests) == ("chat", "gemini-test", 2)
    assert (event.input_tokens, event.output_tokens, event.thought_tokens) == (100, 20, 5)
    assert event.total_tokens == 125
    assert event.actor_subject == "user-1"


def test_record_usage_skips_when_no_model_call_happened() -> None:
    sink = []

    for usage in (None, TokenUsage()):
        asyncio.run(
            usage_service.record_usage(
                tenant_id=None,
                vendor_id=None,
                feature="chat",
                model="m",
                usage=usage,
                factory=lambda: Collector(sink),
            )
        )

    assert sink == []


def test_record_usage_failure_is_logged_not_raised(caplog) -> None:
    with caplog.at_level("ERROR", logger="uvicorn.error"):
        asyncio.run(
            usage_service.record_usage(
                tenant_id=None,
                vendor_id=None,
                feature="chat",
                model="m",
                usage=TokenUsage(requests=1),
                factory=lambda: Collector([], fail=True),
            )
        )

    assert "LLM usage could not be recorded" in caplog.text


# ------------------------------------------------------------
# Assessment worker
# ------------------------------------------------------------


@pytest.fixture
def worker_job(monkeypatch):
    from vendor_risk_analyzer.assessments import worker

    assessment = SimpleNamespace(
        id=uuid4(),
        vendor_id=uuid4(),
        status="running",
        completed_at=None,
        extra_data={"requested_by": "user-1"},
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, model, key):
            return assessment

        async def refresh(self, value):
            return None

        async def commit(self):
            return None

    risk = SimpleNamespace(
        analyze_vendor=AsyncMock(return_value=SimpleNamespace(findings=[])),
        close=Mock(),
        model="gemini-test",
        token_usage=TokenUsage(500, 100, 0, 600, 1),
    )
    record = AsyncMock()
    monkeypatch.setattr(worker, "EmbeddingService", Mock(return_value=SimpleNamespace(close=AsyncMock())))
    monkeypatch.setattr(worker, "RiskAnalysisService", Mock(return_value=risk))
    monkeypatch.setattr(worker, "SemanticRetriever", Mock())
    monkeypatch.setattr(
        worker,
        "AssessmentPersistenceService",
        Mock(return_value=SimpleNamespace(persist=AsyncMock())),
    )
    monkeypatch.setattr(
        worker,
        "RiskPolicyService",
        Mock(return_value=SimpleNamespace(apply_assessment=AsyncMock())),
    )
    monkeypatch.setattr(worker, "set_progress", AsyncMock())
    monkeypatch.setattr(worker, "mark_failed", AsyncMock())
    monkeypatch.setattr(worker, "audit_job", AsyncMock())
    monkeypatch.setattr(worker, "record_job_usage", record)
    return SimpleNamespace(
        worker=worker,
        assessment=assessment,
        factory=Session,
        risk=risk,
        record=record,
    )


def test_completed_assessment_stores_and_records_usage(worker_job) -> None:
    status = asyncio.run(
        worker_job.worker.run_assessment_job(worker_job.factory, worker_job.assessment.id)
    )

    assert status == "completed"
    assert worker_job.assessment.extra_data["llm_usage"] == {
        "model": "gemini-test",
        "input_tokens": 500,
        "output_tokens": 100,
        "thought_tokens": 0,
        "total_tokens": 600,
        "requests": 1,
    }
    worker_job.record.assert_awaited_once_with(
        worker_job.factory, worker_job.assessment.id, worker_job.risk
    )


def test_failed_assessment_still_records_usage(worker_job) -> None:
    worker_job.risk.analyze_vendor.side_effect = RiskAnalysisError("bad output")

    status = asyncio.run(
        worker_job.worker.run_assessment_job(worker_job.factory, worker_job.assessment.id)
    )

    assert status == "failed"
    worker_job.record.assert_awaited_once()
