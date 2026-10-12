"""Batch mode for the apply_risk_policy operations script."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts.ops import apply_risk_policy
from scripts.ops.run import build_command


pytestmark = pytest.mark.unit


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakePolicyService:
    def __init__(self, failing_id):
        self.failing_id = failing_id
        self.applied = []
        self.evaluated = []

    def _result(self, assessment_id):
        if assessment_id == self.failing_id:
            raise RuntimeError("broken assessment")
        return SimpleNamespace(
            proposed_overall_risk="high",
            decisions=[object(), object()],
        )

    async def apply_assessment(self, *, db, assessment_id):
        self.applied.append(assessment_id)
        return self._result(assessment_id)

    async def evaluate_assessment(self, *, db, assessment_id):
        self.evaluated.append(assessment_id)
        return self._result(assessment_id)


def run_batch(monkeypatch, *, write):
    ids = [uuid4(), uuid4(), uuid4()]

    async def outdated(db):
        return [(ids[0], None), (ids[1], "medium"), (ids[2], None)]

    monkeypatch.setattr(apply_risk_policy, "list_outdated_assessments", outdated)
    service = FakePolicyService(failing_id=ids[1])
    lines = []

    result = asyncio.run(
        apply_risk_policy.apply_to_all(
            session_factory=FakeSession,
            service=service,
            write=write,
            out=lambda *parts: lines.append(" ".join(map(str, parts))),
        )
    )
    return ids, service, lines, result


def test_batch_write_continues_after_a_failure(monkeypatch):
    ids, service, lines, result = run_batch(monkeypatch, write=True)

    assert result == (2, 1)
    assert service.applied == ids
    assert service.evaluated == []
    assert any(line.startswith(f"FAIL {ids[1]}") for line in lines)
    assert any("unrated -> high" in line for line in lines)


def test_batch_dry_run_never_writes(monkeypatch):
    ids, service, lines, result = run_batch(monkeypatch, write=False)

    assert result == (2, 1)
    assert service.applied == []
    assert service.evaluated == ids
    assert "DRY RUN: No database rows were modified." in lines


def test_dispatcher_passes_all_to_the_script():
    command = build_command(
        {"OPS_TASK": "apply_risk_policy", "OPS_ASSESSMENT_ID": "all"}
    )

    assert command[-2:] == ["--assessment-id", "all"]
    assert "--write" not in command
