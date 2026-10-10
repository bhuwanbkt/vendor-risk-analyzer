"""Security checks through the production lifespan, bearer gate, and MCP transport."""

from __future__ import annotations

import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from starlette.routing import Mount

pytestmark = pytest.mark.security


@pytest.fixture
def mcp_api(bearer_api, monkeypatch):
    from vendor_risk_analyzer import main
    from vendor_risk_analyzer.config import get_settings
    from vendor_risk_analyzer.mcp import server

    api = bearer_api.api
    monkeypatch.setenv("MCP_ENABLED", "true")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://testserver/mcp")
    get_settings.cache_clear()

    @asynccontextmanager
    async def session_factory():
        yield api.db

    monkeypatch.setattr(server, "EmbeddingService", api.cloud.embedding_factory)
    monkeypatch.setattr(server, "ChatService", api.cloud.chat_factory)
    bundle = server.create_mcp_application(
        get_settings(), session_factory=session_factory
    )
    mount = Mount("/", bundle.app, name="test-mcp")
    main.app.router.routes.append(mount)
    monkeypatch.setattr(main, "mcp_application", bundle)
    try:
        with TestClient(main.app, base_url="https://testserver") as client:
            state = SimpleNamespace(
                client=client, api=api, provider=bearer_api, bundle=bundle
            )

            def headers(roles=("viewer",), **kwargs):
                updates = {"client_id": "mcp-test-client", **kwargs.pop("updates", {})}
                return {
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-11-25",
                    **bearer_api.headers(roles=roles, updates=updates, **kwargs),
                }

            def request(method, params=None, *, roles=("viewer",), extra_headers=None):
                return client.post(
                    "/mcp",
                    headers={**headers(roles), **(extra_headers or {})},
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                )

            def call(name, arguments=None, **kwargs):
                return request(
                    "tools/call", {"name": name, "arguments": arguments or {}}, **kwargs
                )

            state.headers = headers
            state.request = request
            state.call = call
            yield state
    finally:
        main.app.router.routes.remove(mount)


def result(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    return body["result"]


def test_discovery_is_public_and_points_at_fixed_provider(mcp_api):
    response = mcp_api.client.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    metadata = response.json()
    assert metadata["resource"] == "https://testserver/mcp"
    assert metadata["authorization_servers"] == [mcp_api.provider.issuer]
    assert metadata["bearer_methods_supported"] == ["header"]
    assert metadata["scopes_supported"] == [
        "openid", "urn:zitadel:iam:org:projects:roles",
        f"urn:zitadel:iam:org:project:id:{mcp_api.provider.project}:aud",
    ]
    assert mcp_api.provider.requests == []
    mcp_api.api.assert_no_work()


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_anonymous_protocol_requests_are_rejected_before_parsing(mcp_api, method):
    response = mcp_api.client.request(method, "/mcp", content=b"not JSON")
    assert response.status_code == 401
    assert 'resource_metadata="https://testserver/.well-known/oauth-protected-resource/mcp"' in response.headers["www-authenticate"]
    assert response.headers["cache-control"] == "no-store"
    mcp_api.api.assert_no_work()


def test_signed_browser_cookie_cannot_authenticate_mcp(mcp_api):
    cookie = mcp_api.api.authenticate(["admin"])
    mcp_api.client.cookies.set("vendor_risk_session", cookie)
    response = mcp_api.client.post("/mcp", json={"method": "tools/list"})
    assert response.status_code == 401
    mcp_api.api.assert_no_work()


@pytest.mark.parametrize("roles", [(), ("unknown",), ("Admin",)])
def test_unassigned_role_cannot_initialize_or_list_tools(mcp_api, roles):
    response = mcp_api.request("tools/list", roles=roles)
    assert response.status_code == 403
    mcp_api.api.assert_no_work()


@pytest.mark.parametrize("role", ["viewer", "analyst", "admin"])
def test_legacy_handshake_and_fixed_typed_tools_are_available(mcp_api, role):
    handshake = result(mcp_api.request("initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "security-test", "version": "1"},
    }, roles=[role]))
    assert handshake["protocolVersion"] == "2025-11-25"
    tools = result(mcp_api.request("tools/list", roles=[role]))["tools"]
    assert {tool["name"] for tool in tools} == {
        "list_vendors", "get_vendor", "list_vendor_documents",
        "list_vendor_assessments", "get_assessment", "ask_vendor",
    }
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["outputSchema"]["type"] == "object"
        assert tool["annotations"]["readOnlyHint"] is True
    mcp_api.api.assert_no_work()


def test_modern_protocol_envelope_is_supported(mcp_api):
    response = mcp_api.client.post(
        "/mcp",
        headers={
            **mcp_api.headers(), "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/list",
        },
        json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/list",
            "params": {"_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }},
        },
    )
    assert len(result(response)["tools"]) == 6


def test_official_sdk_client_example_works_over_authenticated_http(mcp_api):
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from vendor_risk_analyzer.config import get_settings
    from vendor_risk_analyzer.mcp.server import create_mcp_application

    @asynccontextmanager
    async def session_factory():
        yield mcp_api.api.db

    async def connect():
        bundle = create_mcp_application(get_settings(), session_factory=session_factory)
        async with bundle.app.router.lifespan_context(bundle.app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=bundle.app),
                headers=mcp_api.headers(),
                trust_env=False,
                follow_redirects=False,
            ) as http:
                transport = streamable_http_client("https://testserver/mcp", http_client=http)
                async with Client(transport) as client:
                    assert len((await client.list_tools()).tools) == 6
                    page = await client.call_tool("list_vendors", {"limit": 1})
                    assert page.structured_content["items"][0]["id"] == mcp_api.api.ids["vendor_a"]

    asyncio.run(connect())


@pytest.mark.parametrize("updates,omit", [
    ({}, ()),
    ({"azp": "mcp-test-client"}, ("client_id",)),
    ({"azp": "mcp-test-client"}, ()),
], ids=["client-id", "legacy-azp", "matching-claims"])
def test_signed_oauth_client_claims_authenticate_mcp(mcp_api, updates, omit):
    from vendor_risk_analyzer.config import get_settings
    from vendor_risk_analyzer.mcp.server import ZitadelTokenVerifier

    headers = mcp_api.headers(updates=updates, omit=omit)
    response = mcp_api.client.post(
        "/mcp", headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert len(result(response)["tools"]) == 6
    token = headers["Authorization"].split(" ", 1)[1]
    verified = asyncio.run(ZitadelTokenVerifier(get_settings()).verify_token(token))
    assert verified is not None
    assert verified.client_id == "mcp-test-client"
    mcp_api.api.assert_no_work()


@pytest.mark.parametrize("updates,omit", [
    ({"aud": ["another-project"]}, ()),
    ({"iss": "https://attacker.example"}, ()),
    ({"exp": 1}, ()),
    ({"nonce": "id-token"}, ("jti",)),
    ({"azp": ""}, ()),
    ({"azp": None}, ()),
    ({"client_id": ""}, ()),
    ({"client_id": "   "}, ()),
    ({"client_id": None}, ()),
    ({"client_id": 123}, ()),
    ({"client_id": ["mcp-test-client"]}, ()),
    ({"azp": "another-client"}, ()),
    ({"azp": "mcp-test-client", "client_id": ""}, ()),
    ({}, ("client_id", "azp")),
])
def test_invalid_signed_tokens_cannot_reach_tools(mcp_api, updates, omit):
    response = mcp_api.client.post(
        "/mcp", headers=mcp_api.headers(updates=updates, omit=omit), content=b"not JSON"
    )
    assert response.status_code == 401
    mcp_api.api.assert_no_work()


def test_bad_signature_cannot_fall_back_to_admin_cookie(mcp_api, signing_keys):
    cookie = mcp_api.api.authenticate(["admin"])
    mcp_api.client.cookies.set("vendor_risk_session", cookie)
    response = mcp_api.client.post(
        "/mcp", headers=mcp_api.headers(key=signing_keys[1]), content=b"not JSON"
    )
    assert response.status_code == 401
    mcp_api.api.assert_no_work()


@pytest.mark.parametrize("value", ["Basic abc", "Bearer", "Bearer abc def", ""])
def test_malformed_authorization_is_rejected(mcp_api, value):
    response = mcp_api.client.post("/mcp", headers={"Authorization": value})
    assert response.status_code == 401
    assert mcp_api.provider.requests == []
    mcp_api.api.assert_no_work()


def test_duplicate_authorization_headers_are_rejected(mcp_api):
    token = mcp_api.headers()["Authorization"]
    response = mcp_api.client.post(
        "/mcp", headers=[("Authorization", token), ("Authorization", token)]
    )
    assert response.status_code == 401
    assert mcp_api.provider.requests == []
    mcp_api.api.assert_no_work()


@pytest.mark.parametrize("headers,status", [
    ({"Origin": "https://attacker.example"}, 403),
    ({"Origin": "null"}, 403),
    ({"Host": "attacker.example"}, 421),
])
def test_origin_and_host_are_checked_before_key_fetch(mcp_api, headers, status):
    response = mcp_api.request("tools/list", extra_headers=headers)
    assert response.status_code == status
    assert mcp_api.provider.requests == []
    mcp_api.api.assert_no_work()


def test_provider_outage_returns_retryable_error(mcp_api):
    mcp_api.provider.status = 503
    response = mcp_api.request("tools/list")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "10"
    assert response.headers["cache-control"] == "no-store"
    mcp_api.api.assert_no_work()


def test_vendor_pages_are_bounded_and_stably_ordered(mcp_api):
    first = result(mcp_api.call("list_vendors", {"limit": 1}))["structuredContent"]
    assert [row["id"] for row in first["items"]] == [mcp_api.api.ids["vendor_a"]]
    assert first["next_offset"] == 1
    second = result(mcp_api.call("list_vendors", {"limit": 1, "offset": 1}))["structuredContent"]
    assert [row["id"] for row in second["items"]] == [mcp_api.api.ids["vendor_b"]]
    assert second["next_offset"] is None


@pytest.mark.parametrize("name", ["list_vendor_documents", "list_vendor_assessments"])
@pytest.mark.parametrize("suffix", ["a", "b"])
def test_vendor_lists_never_include_other_vendor_data(mcp_api, name, suffix):
    vendor_id = mcp_api.api.ids[f"vendor_{suffix}"]
    data = result(mcp_api.call(name, {"vendor_id": vendor_id}))["structuredContent"]
    assert data["vendor_id"] == vendor_id
    assert len(data["items"]) == 1
    assert data["items"][0]["vendor_id"] == vendor_id
    other = "b" if suffix == "a" else "a"
    assert mcp_api.api.ids[f"vendor_{other}"] not in json.dumps(data)
    assert "object_key" not in json.dumps(data)


def test_assessment_report_matches_http_and_keeps_findings_separate(mcp_api):
    ids = mcp_api.api.ids
    data = result(mcp_api.call("get_assessment", {
        "vendor_id": ids["vendor_a"], "assessment_id": ids["assessment_a"]
    }))["structuredContent"]
    http = mcp_api.api.client.get(
        f'/api/assessments/{ids["assessment_a"]}', headers=mcp_api.provider.headers(roles=["viewer"])
    )
    assert data == http.json()
    assert data["metadata"]["analysis_summary"] == "Private summary A"
    assert [row["title"] for row in data["findings"]] == ["Private finding A"]


def test_mismatched_assessment_and_vendor_returns_no_report(mcp_api):
    ids = mcp_api.api.ids
    data = result(mcp_api.call("get_assessment", {
        "vendor_id": ids["vendor_b"], "assessment_id": ids["assessment_a"]
    }))
    assert data["isError"] is True
    assert "Private" not in json.dumps(data)
    assert mcp_api.api.db.calls == ["execute"]


@pytest.mark.parametrize("name,arguments", [
    ("get_vendor", {"vendor_id": "bad-uuid"}),
    ("list_vendors", {"limit": 0}),
    ("list_vendors", {"limit": 101}),
    ("list_vendors", {"limit": "50"}),
    ("list_vendors", {"offset": -1}),
    ("list_vendors", {"offset": 10_001}),
    ("ask_vendor", {"question": "x"}),
    ("arbitrary_sql", {"query": "select * from vendors"}),
])
def test_invalid_arguments_and_unknown_tools_do_no_work(mcp_api, name, arguments):
    data = result(mcp_api.call(name, arguments))
    assert data["isError"] is True
    mcp_api.api.assert_no_work()


@pytest.mark.parametrize("role", ["analyst", "admin"])
def test_question_uses_explicit_vendor_and_closes_embedding_client(mcp_api, role):
    ids = mcp_api.api.ids
    data = result(mcp_api.call("ask_vendor", {
        "vendor_id": ids["vendor_b"], "question": "  What evidence is available?  "
    }, roles=[role]))["structuredContent"]
    assert data["vendor_id"] == ids["vendor_b"]
    assert data["answer"] == "Test answer"
    call = mcp_api.api.cloud.chat_factory.return_value.answer.call_args.kwargs
    assert call["vendor_id"] == UUID(ids["vendor_b"])
    assert call["question"] == "What evidence is available?"
    assert call["history"] == []
    mcp_api.api.cloud.embedding_factory.return_value.close.assert_awaited_once()


def test_viewer_question_is_denied_before_database_or_gemini(mcp_api):
    data = result(mcp_api.call("ask_vendor", {
        "vendor_id": mcp_api.api.ids["vendor_a"], "question": "What evidence?"
    }))
    assert data["isError"] is True
    assert "Insufficient permissions" in data["content"][0]["text"]
    mcp_api.api.assert_no_work()


def test_later_viewer_does_not_inherit_analyst_identity(mcp_api):
    arguments = {"vendor_id": mcp_api.api.ids["vendor_a"], "question": "Evidence?"}
    assert not result(mcp_api.call("ask_vendor", arguments, roles=["analyst"]))["isError"]
    mcp_api.api.db.calls.clear()
    for mock in vars(mcp_api.api.cloud).values():
        mock.reset_mock()
    assert result(mcp_api.call("ask_vendor", arguments))["isError"] is True
    mcp_api.api.assert_no_work()
    assert mcp_api.client.post("/mcp", json={}).status_code == 401


def test_concurrent_viewer_does_not_inherit_analyst_authorization(mcp_api):
    from vendor_risk_analyzer.chat.schemas import ChatResponse
    from mcp.server.auth.middleware.auth_context import get_access_token

    started, release = Event(), Event()
    observed_roles = []

    async def pending_answer(**kwargs):
        observed_roles.append(get_access_token().claims["roles"])
        started.set()
        await asyncio.to_thread(release.wait, 5)
        observed_roles.append(get_access_token().claims["roles"])
        return ChatResponse(
            vendor_id=kwargs["vendor_id"], answer="Test answer", model="test", sources=[]
        )

    mcp_api.api.cloud.chat_factory.return_value.answer.side_effect = pending_answer
    arguments = {"vendor_id": mcp_api.api.ids["vendor_a"], "question": "Evidence?"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        analyst = pool.submit(mcp_api.call, "ask_vendor", arguments, roles=["analyst"])
        try:
            assert started.wait(5)
            viewer = result(mcp_api.call("ask_vendor", arguments, roles=["viewer"]))
            assert viewer["isError"] is True
        finally:
            release.set()
        assert not result(analyst.result(timeout=5))["isError"]
    assert observed_roles == [["analyst"], ["analyst"]]
    assert mcp_api.api.db.calls == ["get"]
    mcp_api.api.cloud.chat_factory.return_value.answer.assert_awaited_once()


def test_missing_vendor_and_blank_question_do_not_call_gemini(mcp_api):
    for vendor_id, question in [
        (mcp_api.api.ids["missing"], "Evidence?"),
        (mcp_api.api.ids["vendor_a"], "   "),
    ]:
        data = result(mcp_api.call("ask_vendor", {
            "vendor_id": vendor_id, "question": question
        }, roles=["analyst"]))
        assert data["isError"] is True
    mcp_api.api.cloud.embedding_factory.assert_not_called()
    mcp_api.api.cloud.chat_factory.assert_not_called()


def test_chat_grounding_failure_is_safe_and_closes_resources(mcp_api):
    from vendor_risk_analyzer.chat.service import ChatGroundingError

    mcp_api.api.cloud.chat_factory.return_value.answer.side_effect = ChatGroundingError("private prompt")
    data = result(mcp_api.call("ask_vendor", {
        "vendor_id": mcp_api.api.ids["vendor_a"], "question": "Evidence?"
    }, roles=["analyst"]))
    assert data["isError"] is True
    assert "private prompt" not in json.dumps(data)
    mcp_api.api.cloud.embedding_factory.return_value.close.assert_awaited_once()


@pytest.mark.parametrize("repaired", [True, False])
def test_mcp_chat_handles_citation_failures_with_real_service(mcp_api, monkeypatch, repaired):
    from vendor_risk_analyzer.chat.service import ChatService, UNSUPPORTED_ANSWER
    from vendor_risk_analyzer.mcp import server
    from vendor_risk_analyzer.retrieval.service import RetrievalResult

    vendor_id = mcp_api.api.ids["vendor_a"]
    retrieval = AsyncMock(return_value=[RetrievalResult(
        chunk_id="test-chunk", document_id=mcp_api.api.ids["document_a"],
        sequence=1, content="Notify customers of an incident.", metadata={},
        cosine_distance=0.2, similarity=0.8,
    )])
    cited = "The document requires notification [S1]."
    generation = AsyncMock(side_effect=[
        "Private uncited answer.", cited if repaired else "Private uncited answer [S99].",
    ])
    monkeypatch.setattr(server, "ChatService", ChatService)
    monkeypatch.setattr(server.SemanticRetriever, "search", retrieval)
    monkeypatch.setattr(ChatService, "_generate", generation)

    data = result(mcp_api.call("ask_vendor", {
        "vendor_id": vendor_id, "question": "What is required?",
    }, roles=["analyst"]))

    assert not data.get("isError", False)
    answer = data["structuredContent"]
    assert answer["vendor_id"] == vendor_id
    assert answer["answer"] == (cited if repaired else UNSUPPORTED_ANSWER)
    assert len(answer["sources"]) == int(repaired)
    assert "Private uncited answer" not in json.dumps(data)
    assert retrieval.await_args.kwargs["vendor_id"] == vendor_id
    assert generation.await_count == 2
    mcp_api.api.cloud.embedding_factory.return_value.close.assert_awaited_once()


def test_database_failure_does_not_expose_sql_or_credentials(mcp_api, monkeypatch):
    monkeypatch.setattr(mcp_api.api.db, "execute", AsyncMock(side_effect=OperationalError(
        "private SQL", {}, Exception("database-password")
    )))
    data = result(mcp_api.call("list_vendors"))
    assert data["isError"] is True
    assert "temporarily unavailable" in data["content"][0]["text"]
    assert "database-password" not in json.dumps(data)
    assert "private SQL" not in json.dumps(data)


def test_oversized_protocol_body_is_rejected(mcp_api):
    response = mcp_api.client.post(
        "/mcp", headers={**mcp_api.headers(), "Content-Type": "application/json"},
        content=b"x" * (64 * 1024 + 1),
    )
    assert response.status_code == 413
    mcp_api.api.assert_no_work()


def test_mcp_is_disabled_by_default(security_api):
    assert security_api.client.post("/mcp", json={}).status_code == 404


def test_enabled_mcp_initializes_and_shuts_down_with_the_real_entrypoint(mcp_api, monkeypatch):
    import importlib.util
    from pathlib import Path
    from vendor_risk_analyzer import main

    spec = importlib.util.spec_from_file_location(
        "vendor_risk_analyzer._mcp_startup_test", Path(main.__file__)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    worker = AsyncMock()
    monkeypatch.setattr(module, "run_embedding_worker", worker)
    with TestClient(module.app, base_url="https://testserver") as client:
        assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 200
        assert client.post("/mcp", json={}).status_code == 401
        assert client.get("/sign-in").status_code == 200
    worker.assert_awaited_once()
