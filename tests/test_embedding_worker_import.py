from __future__ import annotations

from pathlib import Path


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
