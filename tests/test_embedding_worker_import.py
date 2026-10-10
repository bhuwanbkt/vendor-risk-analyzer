from __future__ import annotations

import subprocess
import sys


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
