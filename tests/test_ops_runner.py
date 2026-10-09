from __future__ import annotations

import pytest

from scripts.ops.run import (
    build_command,
)


def test_backfill_defaults_to_dry_run() -> None:
    command = build_command(
        {
            "OPS_TASK":
                "backfill_embeddings",
        }
    )

    assert (
        "backfill_embeddings.py"
        in command[1]
    )
    assert "--limit" in command
    assert "--delay-seconds" in command
    assert "--write" not in command


def test_backfill_write_and_document_scope() -> None:
    command = build_command(
        {
            "OPS_TASK":
                "backfill_embeddings",
            "OPS_DOCUMENT_ID":
                "document-123",
            "OPS_LIMIT":
                "10",
            "OPS_DELAY_SECONDS":
                "2",
            "OPS_WRITE":
                "true",
        }
    )

    assert "--document-id" in command
    assert "document-123" in command
    assert "--write" in command


def test_policy_requires_assessment_id() -> None:
    with pytest.raises(
        ValueError,
        match="OPS_ASSESSMENT_ID",
    ):
        build_command(
            {
                "OPS_TASK":
                    "apply_risk_policy",
            }
        )


def test_policy_write_command() -> None:
    command = build_command(
        {
            "OPS_TASK":
                "apply_risk_policy",
            "OPS_ASSESSMENT_ID":
                "assessment-123",
            "OPS_WRITE":
                "1",
        }
    )

    assert (
        "apply_risk_policy.py"
        in command[1]
    )
    assert (
        "assessment-123"
        in command
    )
    assert "--write" in command


def test_unknown_task_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="OPS_TASK",
    ):
        build_command(
            {
                "OPS_TASK":
                    "delete_everything",
            }
        )
