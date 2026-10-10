from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_embedding_worker_imports() -> None:
    from vendor_risk_analyzer.embeddings import worker

    assert callable(
        worker.run_embedding_worker
    )


def test_embedding_worker_uses_application_repository() -> None:
    from vendor_risk_analyzer.embeddings import worker

    assert (
        worker.fetch_chunks.__module__
        == (
            "vendor_risk_analyzer."
            "embeddings.repository"
        )
    )

    source = Path(
        worker.__file__
    ).read_text(
        encoding="utf-8"
    )

    assert "from scripts." not in source
    assert "import scripts." not in source


def test_embedding_worker_imports_outside_repository(tmp_path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "from vendor_risk_analyzer.embeddings.worker "
            "import run_embedding_worker; "
            "assert callable(run_embedding_worker)",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
