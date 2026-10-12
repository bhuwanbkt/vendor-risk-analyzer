"""Batched embedding requests with a safe per-chunk fallback."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vendor_risk_analyzer.embeddings.service import EmbeddingService


pytestmark = pytest.mark.unit


def response(count, dimensions=3):
    return SimpleNamespace(
        embeddings=[
            SimpleNamespace(values=[0.1] * dimensions) for _ in range(count)
        ]
    )


def service(embed_content):
    instance = EmbeddingService.__new__(EmbeddingService)
    instance.model = "test-model"
    instance.dimensions = 3
    instance.max_attempts = 1
    instance.batch_supported = True
    instance.client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(embed_content=embed_content)
        )
    )
    return instance


DOCUMENTS = [("First chunk", "Policy"), ("Second chunk", None), ("Third", "T")]


def test_batch_uses_one_request_for_all_chunks():
    embed_content = AsyncMock(return_value=response(3))
    embedder = service(embed_content)

    vectors = asyncio.run(embedder.embed_documents(DOCUMENTS))

    assert len(vectors) == 3
    embed_content.assert_awaited_once()
    contents = embed_content.await_args.kwargs["contents"]
    assert contents == [
        "title: Policy | text: First chunk",
        "title: none | text: Second chunk",
        "title: T | text: Third",
    ]


def test_wrong_embedding_count_falls_back_to_per_chunk_requests():
    # e.g. a model that aggregates a list into a single embedding
    embed_content = AsyncMock(
        side_effect=[response(1), response(1), response(1), response(1)]
    )
    embedder = service(embed_content)

    vectors = asyncio.run(embedder.embed_documents(DOCUMENTS))

    assert len(vectors) == 3
    assert embed_content.await_count == 4
    assert embedder.batch_supported is False

    # Later batches skip the unsupported batch request entirely.
    embed_content.reset_mock(side_effect=True)
    embed_content.side_effect = [response(1), response(1)]
    asyncio.run(embedder.embed_documents(DOCUMENTS[:2]))
    assert embed_content.await_count == 2


def test_single_chunk_and_empty_input():
    embed_content = AsyncMock(return_value=response(1))
    embedder = service(embed_content)

    assert len(asyncio.run(embedder.embed_documents(DOCUMENTS[:1]))) == 1
    assert asyncio.run(embedder.embed_documents([])) == []
    assert embed_content.await_count == 1
