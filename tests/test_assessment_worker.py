"""Background assessment jobs: success, failure, and shutdown."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from vendor_risk_analyzer.assessments import worker
from vendor_risk_analyzer.risk_analysis.service import (
    RiskAnalysisError,
    RiskAnalysisGroundingError,
)


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self, assessment):
        self.assessment = assessment
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, model, key):
        return self.assessment

    async def refresh(self, value):
        return None

    async def commit(self):
        self.commits += 1


@pytest.fixture
def job(monkeypatch):
    assessment = SimpleNamespace(
        id=uuid4(),
        vendor_id=uuid4(),
        status="running",
        completed_at=None,
        extra_data={"requested_by": "user-1"},
    )
    session = FakeSession(assessment)
    events = []

    analyze = AsyncMock(return_value=SimpleNamespace(findings=[]))
    risk = SimpleNamespace(analyze_vendor=analyze, close=Mock())
    embedding = SimpleNamespace(close=AsyncMock())
    persist = AsyncMock()
    apply_policy = AsyncMock()

    async def set_progress(factory, assessment_id, stage, message):
        events.append(stage)

    monkeypatch.setattr(worker, "EmbeddingService", Mock(return_value=embedding))
    monkeypatch.setattr(worker, "RiskAnalysisService", Mock(return_value=risk))
    monkeypatch.setattr(worker, "SemanticRetriever", Mock())
    monkeypatch.setattr(
        worker,
        "AssessmentPersistenceService",
        Mock(return_value=SimpleNamespace(persist=persist)),
    )
    monkeypatch.setattr(
        worker,
        "RiskPolicyService",
        Mock(return_value=SimpleNamespace(apply_assessment=apply_policy)),
    )
    monkeypatch.setattr(worker, "set_progress", set_progress)
    monkeypatch.setattr(worker, "mark_failed", AsyncMock())
    monkeypatch.setattr(worker, "requeue", AsyncMock())
    monkeypatch.setattr(worker, "audit_job", AsyncMock())

    return SimpleNamespace(
        assessment=assessment,
        factory=lambda: session,
        session=session,
        events=events,
        analyze=analyze,
        risk=risk,
        embedding=embedding,
        persist=persist,
        apply_policy=apply_policy,
    )


def run(job):
    return asyncio.run(
        worker.run_assessment_job(job.factory, job.assessment.id)
    )


def test_successful_job_persists_scores_then_completes(job):
    assert run(job) == "completed"

    job.analyze.assert_awaited_once()
    assert job.analyze.await_args.kwargs["vendor_id"] == str(job.assessment.vendor_id)
    assert job.persist.await_args.kwargs["assessment_id"] == job.assessment.id
    job.apply_policy.assert_awaited_once()
    assert job.events == ["saving", "scoring"]
    assert job.assessment.status == "completed"
    assert job.assessment.completed_at is not None
    assert job.assessment.extra_data["progress"]["stage"] == "completed"
    # Job metadata from the request is preserved.
    assert job.assessment.extra_data["requested_by"] == "user-1"
    job.risk.close.assert_called_once()
    job.embedding.close.assert_awaited_once()


@pytest.mark.parametrize(
    "error,expected",
    [
        (RiskAnalysisError("No retrieval evidence was found for vendor."),
         "Risk analysis failed: No retrieval evidence was found for vendor."),
        (RiskAnalysisGroundingError("bad ids"),
         "The AI response could not be grounded in the vendor's evidence."),
        (RuntimeError("provider stack trace with secrets"),
         "The assessment failed unexpectedly."),
    ],
)
def test_failed_job_records_a_safe_message(job, error, expected):
    job.analyze.side_effect = error

    assert run(job) == "failed"

    worker.mark_failed.assert_awaited_once()
    assert worker.mark_failed.await_args.args[2] == expected
    job.persist.assert_not_awaited()
    job.risk.close.assert_called_once()


def test_shutdown_requeues_the_running_job(job):
    job.analyze.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        run(job)

    worker.requeue.assert_awaited_once()
    worker.mark_failed.assert_not_awaited()


def test_progress_callback_failures_do_not_fail_the_analysis():
    from vendor_risk_analyzer.risk_analysis.service import report_progress

    async def broken(stage, message):
        raise RuntimeError("database down")

    asyncio.run(report_progress(broken, "retrieving", "Searching"))
    asyncio.run(report_progress(None, "retrieving", "Searching"))


def test_job_outcomes_are_audited(job):
    run(job)
    assert worker.audit_job.await_args.args[2] == "assessment.completed"

    worker.audit_job.reset_mock()
    job.analyze.side_effect = RiskAnalysisError("No retrieval evidence was found for vendor.")
    run(job)
    assert worker.audit_job.await_args.args[2] == "assessment.failed"

