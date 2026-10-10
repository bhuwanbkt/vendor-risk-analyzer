from __future__ import annotations

import base64
import importlib
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from vendor_risk_analyzer.db.base import Base
from vendor_risk_analyzer.db.models import Assessment, Document, Finding, Vendor


@compiles(JSONB, "sqlite")
def sqlite_jsonb(type_, compiler, **kwargs):
    # Test-only storage adaptation; PostgreSQL compilation is unchanged.
    return "JSON"


class LocalAsyncSession:
    """Execute real ORM queries locally through the API's async interface."""

    def __init__(self, session):
        self.session = session
        self.calls = []

    async def execute(self, *args, **kwargs):
        self.calls.append("execute")
        return self.session.execute(*args, **kwargs)

    async def scalar(self, *args, **kwargs):
        self.calls.append("scalar")
        return self.session.scalar(*args, **kwargs)

    async def get(self, *args, **kwargs):
        self.calls.append("get")
        return self.session.get(*args, **kwargs)

    def add(self, value):
        self.calls.append("add")
        self.session.add(value)

    async def commit(self):
        self.calls.append("commit")
        self.session.commit()

    async def rollback(self):
        self.calls.append("rollback")
        self.session.rollback()

    async def refresh(self, value):
        self.calls.append("refresh")
        self.session.refresh(value)


@pytest.fixture
def security_api(monkeypatch):
    # Fake configuration must be in place before the application is imported.
    values = {
        "APP_ENV": "test",
        "SESSION_COOKIE_SECURE": "true",
        "API_BEARER_ENABLED": "false",
        "DATABASE_URL": "postgresql://test:test@127.0.0.1:1/test",
        "ZITADEL_ISSUER": "https://identity.example.invalid",
        "ZITADEL_CLIENT_ID": "test-client",
        "ZITADEL_PROJECT_ID": "test-project",
        "ZITADEL_REDIRECT_URI": "https://testserver/auth/callback",
        "ZITADEL_POST_LOGOUT_URI": "https://testserver/",
        "SESSION_SECRET": "security-tests-only-never-a-real-secret",
        "OBJECT_STORAGE_ENDPOINT": "https://storage.example.invalid",
        "OBJECT_STORAGE_REGION": "test-region",
        "OBJECT_STORAGE_ACCESS_KEY_ID": "test-access-key",
        "OBJECT_STORAGE_SECRET_ACCESS_KEY": "test-secret-key",
        "GEMINI_API_KEY": "test-not-a-real-api-key",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    from vendor_risk_analyzer.config import get_settings

    get_settings.cache_clear()
    main = importlib.import_module("vendor_risk_analyzer.main")
    from vendor_risk_analyzer.api import assessments, chat, documents
    from vendor_risk_analyzer.chat.schemas import ChatResponse
    from vendor_risk_analyzer.db.session import get_db

    def reject_network(*args, **kwargs):
        raise AssertionError("Security tests must not call external services")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_network)
    monkeypatch.setattr(main, "run_embedding_worker", AsyncMock())

    ids = {
        name: str(UUID(int=index))
        for index, name in enumerate(
            [
                "vendor_a",
                "vendor_b",
                "document_a",
                "document_b",
                "assessment_a",
                "assessment_b",
                "missing",
            ],
            start=1,
        )
    }
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    for suffix in ("a", "b"):
        vendor_id = UUID(ids[f"vendor_{suffix}"])
        assessment_id = UUID(ids[f"assessment_{suffix}"])
        session.add(
            Vendor(id=vendor_id, name=f"Vendor {suffix.upper()}", status="active")
        )
        session.add(
            Document(
                id=UUID(ids[f"document_{suffix}"]),
                vendor_id=vendor_id,
                filename=f"private-{suffix}.txt",
                file_type="txt",
                mime_type="text/plain",
                size_bytes=5,
                object_key=f"vendors/{vendor_id}/private-{suffix}.txt",
                sha256=suffix * 64,
                status="uploaded",
            )
        )
        session.add(
            Assessment(
                id=assessment_id,
                vendor_id=vendor_id,
                status="completed",
                assessment_type="vendor_risk",
                overall_risk="medium",
                extra_data={"analysis_summary": f"Private summary {suffix.upper()}"},
            )
        )
        session.add(
            Finding(
                assessment_id=assessment_id,
                category="incident_response",
                severity="medium",
                title=f"Private finding {suffix.upper()}",
                description=f"Private evidence {suffix.upper()}",
            )
        )
    session.commit()
    db = LocalAsyncSession(session)

    async def local_database():
        yield db

    # Authentication, authorization, CSRF, routing, and session middleware stay real.
    main.app.dependency_overrides[get_db] = local_database
    cloud = SimpleNamespace(
        upload_url=Mock(return_value="https://storage.example.invalid/presigned"),
        object_metadata=Mock(return_value={"ContentLength": 5, "ETag": "test-etag"}),
        ingest=AsyncMock(),
        embedding_factory=Mock(),
        chat_factory=Mock(),
        risk_factory=Mock(),
        persistence_factory=Mock(),
        policy_factory=Mock(),
    )

    async def ingest(**kwargs):
        document = kwargs["document"]
        document.status = "embedding_pending"
        return document

    async def answer(**kwargs):
        return ChatResponse(
            vendor_id=kwargs["vendor_id"],
            answer="Test answer",
            model="test",
            sources=[],
        )

    cloud.ingest.side_effect = ingest
    cloud.embedding_factory.return_value = SimpleNamespace(close=AsyncMock())
    cloud.chat_factory.return_value = SimpleNamespace(
        answer=AsyncMock(side_effect=answer)
    )
    analysis = SimpleNamespace(raw_finding_count=1, normalized_finding_count=1)
    cloud.risk_factory.return_value = SimpleNamespace(
        analyze_vendor=AsyncMock(return_value=analysis), close=Mock()
    )
    cloud.persistence_factory.return_value = SimpleNamespace(
        persist=AsyncMock(
            return_value=SimpleNamespace(assessment_id=UUID(ids["assessment_a"]))
        )
    )
    cloud.policy_factory.return_value = SimpleNamespace(
        apply_assessment=AsyncMock(
            return_value=SimpleNamespace(proposed_overall_risk="medium")
        )
    )
    for module in (chat, assessments):
        monkeypatch.setattr(module, "EmbeddingService", cloud.embedding_factory)
    monkeypatch.setattr(documents, "generate_upload_url", cloud.upload_url)
    monkeypatch.setattr(documents, "get_object_metadata", cloud.object_metadata)
    monkeypatch.setattr(documents, "ingest_document", cloud.ingest)
    monkeypatch.setattr(chat, "ChatService", cloud.chat_factory)
    monkeypatch.setattr(assessments, "RiskAnalysisService", cloud.risk_factory)
    monkeypatch.setattr(
        assessments, "AssessmentPersistenceService", cloud.persistence_factory
    )
    monkeypatch.setattr(assessments, "RiskPolicyService", cloud.policy_factory)

    signer = TimestampSigner(main.settings.session_secret)
    with TestClient(
        main.app, base_url="https://testserver", follow_redirects=False
    ) as client:

        def authenticate(roles=None, *, user=None, csrf_token="test-csrf-token"):
            if user is None:
                user = {
                    "sub": "test-user",
                    "name": "Test User",
                    "email": "user@example.invalid",
                    "roles": roles or [],
                }
            data = {"user": user}
            if csrf_token is not None:
                data["csrf_token"] = csrf_token
            raw = base64.b64encode(json.dumps(data).encode())
            cookie = signer.sign(raw).decode()
            client.cookies.set("vendor_risk_session", cookie)
            return cookie

        def assert_no_work():
            assert db.calls == []
            for mock in vars(cloud).values():
                mock.assert_not_called()

        yield SimpleNamespace(
            client=client,
            ids=ids,
            db=db,
            cloud=cloud,
            signer=signer,
            authenticate=authenticate,
            assert_no_work=assert_no_work,
        )

    main.app.dependency_overrides.pop(get_db, None)
    session.close()
    engine.dispose()
    get_settings.cache_clear()
