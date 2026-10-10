from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from vendor_risk_analyzer.chat.service import (
    ChatService,
    ChatServiceError,
    INSUFFICIENT_EVIDENCE_ANSWER,
    UNSUPPORTED_ANSWER,
)
from vendor_risk_analyzer.retrieval.service import RetrievalResult

pytestmark = pytest.mark.unit

VENDOR_ID = UUID(int=1)
QUESTION = "Are there conflicting requirements?"


@pytest.fixture
def chat(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("CHAT_LLM_MODEL", "test-chat-model")
    evidence = [
        RetrievalResult(
            chunk_id=f"chunk-{index}",
            document_id=f"document-{index}",
            sequence=index,
            content=f"Notify customers within {index * 24} hours.",
            metadata={},
            cosine_distance=0.2,
            similarity=0.8,
        )
        for index in (1, 2)
    ]
    service = ChatService(
        retriever=SimpleNamespace(search=AsyncMock(return_value=evidence))
    )
    service._generate = AsyncMock()
    return service


def answer(chat):
    return asyncio.run(chat.answer(
        db=None, vendor_id=VENDOR_ID, question=QUESTION, history=[]
    ))


def test_cited_answer_returns_only_current_cited_sources_without_retry(chat):
    text = "The timelines differ: 24 hours [S1] and 48 hours [S2]. See [S2]."
    chat._generate.return_value = text

    response = answer(chat)

    assert response.answer == text
    assert response.vendor_id == VENDOR_ID
    assert response.model == "test-chat-model"
    assert [source.chunk_id for source in response.sources] == ["chunk-1", "chunk-2"]
    chat._generate.assert_awaited_once()
    chat.retriever.search.assert_awaited_once_with(
        db=None, query=QUESTION, vendor_id=str(VENDOR_ID), limit=5
    )


def test_no_evidence_returns_refusal_without_calling_model(chat):
    chat.retriever.search.return_value = []

    response = answer(chat)

    assert response.answer == INSUFFICIENT_EVIDENCE_ANSWER
    assert response.sources == []
    chat._generate.assert_not_awaited()


def test_exact_insufficient_evidence_refusal_does_not_require_citations(chat):
    chat._generate.return_value = INSUFFICIENT_EVIDENCE_ANSWER

    response = answer(chat)

    assert response.answer == INSUFFICIENT_EVIDENCE_ANSWER
    assert response.sources == []
    chat._generate.assert_awaited_once()


@pytest.mark.parametrize("rejected", [
    "There are no conflicting requirements.",
    "Notify customers within 24 hours [S0].",
    "Notify customers within 24 hours [S3].",
    "Notify customers within 24 hours [S1], and 72 hours [S99].",
    INSUFFICIENT_EVIDENCE_ANSWER + " This vendor is certified.",
])
def test_uncited_or_invalid_reply_is_regenerated_against_same_evidence(chat, rejected):
    repaired = "One source specifies 24 hours [S1]."
    chat._generate.side_effect = [rejected, repaired]

    response = answer(chat)

    assert response.answer == repaired
    assert [source.chunk_id for source in response.sources] == ["chunk-1"]
    assert chat._generate.await_count == 2
    chat.retriever.search.assert_awaited_once()


def test_repair_can_return_exact_insufficient_evidence_refusal(chat):
    chat._generate.side_effect = [
        "There is insufficient evidence.", INSUFFICIENT_EVIDENCE_ANSWER,
    ]

    response = answer(chat)

    assert response.answer == INSUFFICIENT_EVIDENCE_ANSWER
    assert response.sources == []
    assert chat._generate.await_count == 2


@pytest.mark.parametrize("rejected", [
    "Private unsupported claim: the vendor is certified.",
    "Private unsupported claim [S0].",
    "Private unsupported claim [S3].",
    "Private unsupported claim [S1] [S99].",
    INSUFFICIENT_EVIDENCE_ANSWER + " Private unsupported claim.",
])
def test_two_failed_replies_return_fixed_fallback_without_model_text(chat, rejected):
    chat._generate.side_effect = [rejected, rejected]

    response = answer(chat)

    assert response.answer == UNSUPPORTED_ANSWER
    assert response.sources == []
    assert "Private" not in response.model_dump_json()
    assert chat._generate.await_count == 2
    chat.retriever.search.assert_awaited_once()


def test_validation_logs_reason_without_question_evidence_or_model_text(chat, caplog):
    chat._generate.side_effect = [
        "Private model answer.", "Private model answer [S99].",
    ]
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        answer(chat)

    assert "did not include evidence citations" in caplog.text
    assert "invalid source citation" in caplog.text
    assert "Private model answer" not in caplog.text
    assert QUESTION not in caplog.text
    assert "Notify customers" not in caplog.text


@pytest.mark.parametrize("replies", [
    [ChatServiceError("Provider unavailable")],
    ["An uncited reply.", ChatServiceError("Provider unavailable")],
])
def test_provider_failures_still_propagate(chat, replies):
    chat._generate.side_effect = replies

    with pytest.raises(ChatServiceError, match="Provider unavailable"):
        answer(chat)

    assert chat._generate.await_count == len(replies)
