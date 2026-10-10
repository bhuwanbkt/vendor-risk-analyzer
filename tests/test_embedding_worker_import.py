from __future__ import annotations


def test_embedding_worker_imports_after_ops_layout() -> None:
    from vendor_risk_analyzer.embeddings import worker

    assert callable(
        worker.run_embedding_worker
    )
