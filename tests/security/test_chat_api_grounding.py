"""Exercise citation repair and refusal through the authenticated chat API."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from vendor_risk_analyzer.chat.service import (
    ChatService,
    INSUFFICIENT_EVIDENCE_ANSWER,
    UNSUPPORTED_ANSWER,
)
from vendor_risk_analyzer.retrieval.service import RetrievalResult

pytestmark = pytest.mark.security


@pytest.mark.parametrize("replies,expected_answer,source_count", [
    (
        ["An uncited answer.", "The document requires notification [S1]."],
        "The document requires notification [S1].", 1,
    ),
    (["An uncited answer.", "Another uncited answer."], UNSUPPORTED_ANSWER, 0),
    ([INSUFFICIENT_EVIDENCE_ANSWER], INSUFFICIENT_EVIDENCE_ANSWER, 0),
])
def test_chat_api_handles_citation_failures_with_real_service(
    security_api, monkeypatch, replies, expected_answer, source_count
):
    from vendor_risk_analyzer.api import chat

    security_api.authenticate(["analyst"])
    vendor_id = security_api.ids["vendor_a"]
    retrieval = AsyncMock(return_value=[RetrievalResult(
        chunk_id="test-chunk", document_id=security_api.ids["document_a"],
        sequence=1, content="Notify customers of an incident.", metadata={},
        cosine_distance=0.2, similarity=0.8,
    )])
    generation = AsyncMock(side_effect=replies)
    monkeypatch.setattr(chat, "ChatService", ChatService)
    monkeypatch.setattr(chat.SemanticRetriever, "search", retrieval)
    monkeypatch.setattr(ChatService, "_generate", generation)

    response = security_api.client.post(
        "/api/chat",
        json={"vendor_id": vendor_id, "question": "What is required?"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["vendor_id"] == vendor_id
    assert data["answer"] == expected_answer
    assert len(data["sources"]) == source_count
    assert retrieval.await_args.kwargs["vendor_id"] == vendor_id
    assert generation.await_count == len(replies)
    security_api.cloud.embedding_factory.return_value.close.assert_awaited_once()
