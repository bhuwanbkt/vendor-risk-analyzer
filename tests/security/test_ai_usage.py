"""AI token usage: recorded per chat answer, shown only to the company's admins."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

from vendor_risk_analyzer.chat.service import ChatService
from vendor_risk_analyzer.db.models import LlmUsageEvent, Tenant
from vendor_risk_analyzer.retrieval.service import RetrievalResult

pytestmark = pytest.mark.security


def gemini_reply(text: str, prompt_tokens: int, output_tokens: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {
                "promptTokenCount": prompt_tokens,
                "candidatesTokenCount": output_tokens,
                "totalTokenCount": prompt_tokens + output_tokens,
            },
        },
    )


def test_chat_records_tokens_for_every_model_call(security_api, monkeypatch):
    from vendor_risk_analyzer.api import chat
    from vendor_risk_analyzer.chat import service as chat_service_module

    security_api.authenticate(["analyst"])
    vendor_id = security_api.ids["vendor_a"]
    replies = iter(
        [
            # Rejected: no citation. Its tokens still count.
            gemini_reply("An uncited answer.", 900, 40),
            gemini_reply("Customers are notified [S1].", 950, 60),
        ]
    )
    transport = httpx.MockTransport(lambda request: next(replies))
    real_client = httpx.AsyncClient

    monkeypatch.setattr(
        chat_service_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(chat, "ChatService", ChatService)
    monkeypatch.setattr(
        chat.SemanticRetriever,
        "search",
        AsyncMock(
            return_value=[
                RetrievalResult(
                    chunk_id="chunk-1",
                    document_id=security_api.ids["document_a"],
                    sequence=1,
                    content="Customers are notified of incidents.",
                    metadata={},
                    cosine_distance=0.2,
                    similarity=0.8,
                )
            ]
        ),
    )

    response = security_api.client.post(
        "/api/chat",
        json={"vendor_id": vendor_id, "question": "Are customers notified?"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )

    assert response.status_code == 200
    (event,) = security_api.usage_events
    assert event.tenant_id == security_api.tenant.id
    assert str(event.vendor_id) == vendor_id
    assert event.feature == "chat"
    assert event.actor_subject == "test-user"
    assert (event.requests, event.input_tokens, event.output_tokens) == (2, 1850, 100)
    assert event.total_tokens == 1950


def test_rejected_chat_request_records_no_usage(security_api):
    security_api.authenticate(["viewer"])

    response = security_api.client.post(
        "/api/chat",
        json={"vendor_id": security_api.ids["vendor_a"], "question": "Hi?"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )

    assert response.status_code == 403
    assert security_api.usage_events == []


def add_usage(session, tenant_id, *, feature, model, tokens, days_ago=0):
    session.add(
        LlmUsageEvent(
            tenant_id=tenant_id,
            vendor_id=uuid4(),
            feature=feature,
            model=model,
            requests=1,
            input_tokens=tokens,
            output_tokens=tokens // 10,
            thought_tokens=0,
            total_tokens=tokens + tokens // 10,
            created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        )
    )


def test_system_page_shows_only_this_companys_recent_usage(security_api, monkeypatch):
    monkeypatch.delenv("LLM_INPUT_PRICE_PER_MILLION_TOKENS", raising=False)
    monkeypatch.delenv("LLM_OUTPUT_PRICE_PER_MILLION_TOKENS", raising=False)
    session = security_api.db.session
    other = Tenant(id=UUID(int=200), name="Other", zitadel_org_id="other-org")
    session.add(other)
    session.flush()
    tenant_id = security_api.tenant.id
    add_usage(session, tenant_id, feature="assessment", model="gemini-a", tokens=12_000)
    add_usage(session, tenant_id, feature="assessment", model="gemini-a", tokens=8_000)
    add_usage(session, tenant_id, feature="chat", model="gemini-a", tokens=3_000)
    # Outside the 30-day window, and another company's usage.
    add_usage(session, tenant_id, feature="chat", model="gemini-a", tokens=777_777, days_ago=45)
    add_usage(session, other.id, feature="chat", model="gemini-other", tokens=555_555)
    session.commit()
    security_api.authenticate(["admin"])

    response = security_api.client.get("/admin/system")

    assert response.status_code == 200
    page = response.text
    assert "AI Usage" in page
    assert "<td>20,000</td>" in page  # assessment input tokens
    assert "<td>3,000</td>" in page  # chat input tokens
    assert "<td>23,000</td>" in page  # total input tokens
    assert "777,777" not in page
    assert "555,555" not in page
    assert "gemini-other" not in page
    assert "LLM_INPUT_PRICE_PER_MILLION_TOKENS" in page
    assert "Est. cost" not in page


def test_system_page_estimates_cost_when_prices_are_set(security_api, monkeypatch):
    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MILLION_TOKENS", "1.00")
    monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MILLION_TOKENS", "10.00")
    session = security_api.db.session
    add_usage(
        session, security_api.tenant.id, feature="chat", model="gemini-a", tokens=1_000_000
    )
    session.commit()
    security_api.authenticate(["admin"])

    response = security_api.client.get("/admin/system")

    assert response.status_code == 200
    # 1M input at $1 + 100k output at $10 = $2.
    assert "Est. cost (USD)" in response.text
    assert "2.0000" in response.text


def test_system_page_survives_missing_usage_table(security_api, monkeypatch):
    from vendor_risk_analyzer.web import routes

    monkeypatch.setattr(
        routes, "summarize_usage", AsyncMock(side_effect=RuntimeError("no table"))
    )
    security_api.authenticate(["admin"])

    response = security_api.client.get("/admin/system")

    assert response.status_code == 200
    assert "Run the latest database migration" in response.text
