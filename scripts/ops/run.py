from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from collections.abc import Mapping


TRUTHY = {
    "1",
    "true",
    "yes",
    "on",
}

OPS_DIR = Path(__file__).resolve().parent


def _is_truthy(
    value: str | None,
) -> bool:
    return (
        str(
            value or ""
        )
        .strip()
        .lower()
        in TRUTHY
    )


def build_command(
    env: Mapping[
        str,
        str,
    ],
) -> list[str]:
    task = (
        env.get(
            "OPS_TASK",
            "",
        )
        .strip()
        .lower()
    )

    if task == "backfill_embeddings":
        command = [
            sys.executable,
            str(
                OPS_DIR
                / "backfill_embeddings.py"
            ),
        ]

        limit = env.get(
            "OPS_LIMIT",
            "50",
        ).strip()

        delay = env.get(
            "OPS_DELAY_SECONDS",
            "4",
        ).strip()

        command.extend(
            [
                "--limit",
                limit,
                "--delay-seconds",
                delay,
            ]
        )

        document_id = (
            env.get(
                "OPS_DOCUMENT_ID",
                "",
            )
            .strip()
        )

        if document_id:
            command.extend(
                [
                    "--document-id",
                    document_id,
                ]
            )

        if _is_truthy(
            env.get(
                "OPS_WRITE"
            )
        ):
            command.append(
                "--write"
            )

        return command

    if task == "apply_risk_policy":
        assessment_id = (
            env.get(
                "OPS_ASSESSMENT_ID",
                "",
            )
            .strip()
        )

        if not assessment_id:
            raise ValueError(
                "OPS_ASSESSMENT_ID is "
                "required for "
                "apply_risk_policy."
            )

        command = [
            sys.executable,
            str(
                OPS_DIR
                / "apply_risk_policy.py"
            ),
            "--assessment-id",
            assessment_id,
        ]

        if _is_truthy(
            env.get(
                "OPS_WRITE"
            )
        ):
            command.append(
                "--write"
            )

        return command

    raise ValueError(
        "OPS_TASK must be one of: "
        "backfill_embeddings, "
        "apply_risk_policy."
    )


def main() -> int:
    try:
        command = (
            build_command(
                os.environ
            )
        )

    except ValueError as exc:
        print(
            f"ERROR: {exc}"
        )
        return 2

    print(
        "Northflank utility task:",
        os.environ.get(
            "OPS_TASK"
        ),
    )

    print(
        "Write mode:",
        _is_truthy(
            os.environ.get(
                "OPS_WRITE"
            )
        ),
    )

    completed = subprocess.run(
        command,
        check=False,
    )

    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
