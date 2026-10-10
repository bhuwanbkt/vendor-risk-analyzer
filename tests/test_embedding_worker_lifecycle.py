from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from vendor_risk_analyzer.embeddings import worker
from vendor_risk_analyzer.embeddings.service import EmbeddingError


pytestmark = pytest.mark.unit


@pytest.fixture
def runtime(monkeypatch):
    engine = SimpleNamespace(dispose=AsyncMock())
    service = SimpleNamespace(close=AsyncMock())
    engine_factory = Mock(return_value=engine)
    service_factory = Mock(return_value=service)
    monkeypatch.setenv("EMBEDDING_WORKER_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_WORKER_POLL_SECONDS", "2")
    monkeypatch.setenv("EMBEDDING_WORKER_BATCH_SIZE", "20")
    monkeypatch.setenv("EMBEDDING_WORKER_DELAY_SECONDS", "4")
    monkeypatch.setenv("EMBEDDING_WORKER_STALE_MINUTES", "30")
    monkeypatch.setattr(worker, "get_database_url", Mock(return_value="unused"))
    monkeypatch.setattr(worker, "create_async_engine", engine_factory)
    monkeypatch.setattr(worker, "EmbeddingService", service_factory)
    monkeypatch.setattr(worker, "recover_stale_documents", AsyncMock(return_value=0))
    monkeypatch.setattr(
        worker,
        "claim_next_document",
        AsyncMock(side_effect=asyncio.CancelledError),
    )
    monkeypatch.setattr(worker.asyncio, "sleep", AsyncMock())
    return SimpleNamespace(
        engine=engine,
        service=service,
        engine_factory=engine_factory,
        service_factory=service_factory,
    )


@pytest.mark.parametrize("value", ["false", "0", "off", "no", " FALSE "])
def test_disabled_worker_does_not_create_external_clients(
    monkeypatch, runtime, value
) -> None:
    monkeypatch.setenv("EMBEDDING_WORKER_ENABLED", value)
    # A disabled worker must also ignore irrelevant or missing configuration.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDING_WORKER_POLL_SECONDS", "invalid")

    asyncio.run(worker.run_embedding_worker())

    runtime.engine_factory.assert_not_called()
    runtime.service_factory.assert_not_called()
    worker.recover_stale_documents.assert_not_called()


@pytest.mark.parametrize("value", ["invalid", "", "2"])
def test_invalid_worker_flag_is_rejected_before_creating_clients(
    monkeypatch, runtime, value
) -> None:
    monkeypatch.setenv("EMBEDDING_WORKER_ENABLED", value)

    with pytest.raises(ValueError, match="must be a boolean"):
        asyncio.run(worker.run_embedding_worker())

    runtime.engine_factory.assert_not_called()
    runtime.service_factory.assert_not_called()


def test_worker_remains_enabled_when_flag_is_unset(monkeypatch, runtime) -> None:
    monkeypatch.delenv("EMBEDDING_WORKER_ENABLED")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.run_embedding_worker())

    worker.recover_stale_documents.assert_awaited_once()
    runtime.service.close.assert_awaited_once()
    runtime.engine.dispose.assert_awaited_once()


def test_startup_database_failure_is_retried_before_processing(
    monkeypatch, runtime
) -> None:
    recovery = AsyncMock(side_effect=[ConnectionError("database waking up"), 1])
    monkeypatch.setattr(worker, "recover_stale_documents", recovery)
    monkeypatch.setattr(worker, "process_document", AsyncMock(return_value="ready"))
    monkeypatch.setattr(
        worker,
        "claim_next_document",
        AsyncMock(side_effect=["document-123", asyncio.CancelledError]),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.run_embedding_worker())

    assert recovery.await_count == 2
    worker.asyncio.sleep.assert_awaited_once_with(2.0)
    worker.process_document.assert_awaited_once_with(
        runtime.engine,
        runtime.service,
        document_id="document-123",
        batch_size=20,
        delay_seconds=4.0,
    )
    runtime.service.close.assert_awaited_once()
    runtime.engine.dispose.assert_awaited_once()


def test_worker_recovers_documents_that_become_stale_after_startup(
    monkeypatch, runtime
) -> None:
    # The interrupted document is too recent at startup, then becomes stale.
    monkeypatch.setattr(worker, "process_document", AsyncMock(return_value="ready"))
    monkeypatch.setattr(
        worker,
        "time",
        SimpleNamespace(
            monotonic=Mock(side_effect=[0.0, 0.0, 0.0, 1801.0, 1801.0, 1801.0])
        ),
    )
    monkeypatch.setattr(
        worker,
        "recover_stale_documents",
        AsyncMock(side_effect=[0, 1]),
    )
    monkeypatch.setattr(
        worker,
        "claim_next_document",
        AsyncMock(side_effect=[None, "document-123", asyncio.CancelledError]),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.run_embedding_worker())

    assert worker.recover_stale_documents.await_count == 2
    assert worker.process_document.await_count == 1


def test_client_initialization_failure_disposes_database_engine(runtime) -> None:
    runtime.service_factory.side_effect = EmbeddingError("missing API key")

    with pytest.raises(EmbeddingError, match="missing API key"):
        asyncio.run(worker.run_embedding_worker())

    runtime.engine.dispose.assert_awaited_once()


def test_client_close_failure_still_disposes_database_engine(runtime) -> None:
    runtime.service.close.side_effect = RuntimeError("client close failed")

    with pytest.raises(RuntimeError, match="client close failed"):
        asyncio.run(worker.run_embedding_worker())

    runtime.engine.dispose.assert_awaited_once()


def test_shutdown_restores_document_state_without_marking_failed(
    monkeypatch, runtime
) -> None:
    monkeypatch.setattr(
        worker, "fetch_chunks", AsyncMock(side_effect=asyncio.CancelledError)
    )
    finalize = AsyncMock(return_value="embedding_pending")
    mark_failed = AsyncMock()
    monkeypatch.setattr(worker, "finalize_document", finalize)
    monkeypatch.setattr(worker, "mark_document_failed", mark_failed)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            worker.process_document(
                runtime.engine,
                runtime.service,
                document_id="document-123",
                batch_size=20,
                delay_seconds=4.0,
            )
        )

    finalize.assert_awaited_once_with(
        runtime.engine, document_id="document-123"
    )
    mark_failed.assert_not_awaited()


def test_processing_renews_document_claim_for_each_batch(monkeypatch, runtime) -> None:
    chunks = [
        {"id": "chunk-1", "metadata": {}, "content": "Evidence one", "sequence": 0},
        {"id": "chunk-2", "metadata": {}, "content": "Evidence two", "sequence": 1},
    ]
    monkeypatch.setattr(
        worker, "fetch_chunks", AsyncMock(side_effect=[[chunks[0]], [chunks[1]], []])
    )
    monkeypatch.setattr(worker, "save_embedding", AsyncMock())
    monkeypatch.setattr(
        worker,
        "verify_embedding",
        AsyncMock(return_value={"has_embedding": True, "dimensions": 768}),
    )
    monkeypatch.setattr(
        worker,
        "get_document_embedding_state",
        AsyncMock(side_effect=[
            {"total_chunks": 2, "embedded_chunks": 1, "missing_embeddings": 1},
            {"total_chunks": 2, "embedded_chunks": 2, "missing_embeddings": 0},
        ]),
    )
    monkeypatch.setattr(worker, "finalize_document", AsyncMock(return_value="ready"))
    renew_claim = AsyncMock()
    monkeypatch.setattr(worker, "update_document_status", renew_claim)

    async def embed_after_renewal(**kwargs):
        # Claim renewal must precede work on each batch.
        assert renew_claim.await_count == service.embed_document.await_count
        return [0.1] * 768

    service = SimpleNamespace(
        model="test-model",
        dimensions=768,
        embed_document=AsyncMock(side_effect=embed_after_renewal),
    )
    result = asyncio.run(
        worker.process_document(
            runtime.engine,
            service,
            document_id="document-123",
            batch_size=1,
            delay_seconds=0,
        )
    )

    assert result == "ready"
    assert renew_claim.await_count == 2
    assert all(
        call.kwargs == {"document_id": "document-123", "status": "embedding"}
        for call in renew_claim.await_args_list
    )
