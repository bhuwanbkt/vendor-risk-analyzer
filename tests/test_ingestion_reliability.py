"""Regression tests for document ingestion failure handling.

The fake session mimics the AsyncSession behaviour that caused documents to
stay in "processing": rollback() expires loaded objects, and reading an
expired attribute raises (MissingGreenlet in production) until refresh().
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from vendor_risk_analyzer.ingestion.parsers.base import ParsedElement
from vendor_risk_analyzer.schemas.document import (
    STALE_PROCESSING_AFTER,
    DocumentResponse,
    is_stale_processing,
)


pytestmark = pytest.mark.unit


REQUIRED_SETTINGS = {
    "DATABASE_URL": "postgresql://test:test@127.0.0.1:1/test",
    "ZITADEL_ISSUER": "https://identity.example.invalid",
    "ZITADEL_CLIENT_ID": "client",
    "ZITADEL_PROJECT_ID": "project",
    "ZITADEL_REDIRECT_URI": "http://localhost:8000/auth/callback",
    "ZITADEL_POST_LOGOUT_URI": "http://localhost:8000/",
    "SESSION_SECRET": "test-secret",
    "OBJECT_STORAGE_ENDPOINT": "http://storage.invalid",
    "OBJECT_STORAGE_REGION": "us-east-1",
    "OBJECT_STORAGE_ACCESS_KEY_ID": "test-access",
    "OBJECT_STORAGE_SECRET_ACCESS_KEY": "test-secret",
}


@pytest.fixture
def ingestion(monkeypatch):
    for name, value in REQUIRED_SETTINGS.items():
        monkeypatch.setenv(name, value)
    from vendor_risk_analyzer.config import get_settings

    get_settings.cache_clear()
    module = importlib.import_module("vendor_risk_analyzer.ingestion.service")
    monkeypatch.setattr(module, "download_object", lambda key: RAW_CONTENT)
    monkeypatch.setattr(
        module,
        "get_object_metadata",
        lambda key: {"ContentLength": len(RAW_CONTENT)},
    )
    yield module
    get_settings.cache_clear()


RAW_CONTENT = b"raw bytes"


class ExpiredAttributeError(RuntimeError):
    pass


class FakeDocument:
    _loaded = ("id", "filename", "file_type", "object_key", "status", "extra_data")

    def __init__(self):
        self._expired = False
        self.id = uuid4()
        self.filename = "policy.pdf"
        self.file_type = "pdf"
        self.object_key = "vendors/v/raw/d/policy.pdf"
        self.status = "uploaded"
        self.extra_data = {"etag": "abc"}
        self.size_bytes = len(RAW_CONTENT)
        self.sha256 = hashlib.sha256(RAW_CONTENT).hexdigest()

    def __getattribute__(self, name):
        if name in FakeDocument._loaded and object.__getattribute__(self, "_expired"):
            raise ExpiredAttributeError(f"implicit load of expired {name!r}")
        return object.__getattribute__(self, name)


class ExpiringSession:
    def __init__(self, document, *, refresh_error=None):
        self.document = document
        self.refresh_error = refresh_error
        self.committed_statuses = []

    async def commit(self):
        self.committed_statuses.append(self.document.status)

    async def rollback(self):
        self.document._expired = True

    async def refresh(self, value):
        if self.refresh_error is not None:
            raise self.refresh_error
        value._expired = False

    async def execute(self, *args, **kwargs):
        return None

    async def flush(self):
        return None

    def add(self, value):
        return None


def parser(parse):
    return SimpleNamespace(parse=parse, parser_version="test-1")


def test_parse_failure_marks_document_failed(ingestion, monkeypatch):
    def broken_parse(content):
        raise ValueError("Document contained no parseable content")

    monkeypatch.setattr(ingestion, "get_parser", lambda ext: parser(broken_parse))
    document = FakeDocument()
    db = ExpiringSession(document)

    with pytest.raises(ValueError, match="no parseable content"):
        asyncio.run(ingestion.ingest_document(document=document, db=db))

    assert db.committed_statuses == ["processing", "failed"]
    assert document.status == "failed"
    assert document.extra_data == {
        "etag": "abc",
        "ingestion_error": "Document contained no parseable content",
    }


def test_original_error_survives_failed_status_update(ingestion, monkeypatch):
    def broken_parse(content):
        raise ValueError("bad file")

    monkeypatch.setattr(ingestion, "get_parser", lambda ext: parser(broken_parse))
    document = FakeDocument()
    db = ExpiringSession(document, refresh_error=RuntimeError("database down"))

    with pytest.raises(ValueError, match="bad file"):
        asyncio.run(ingestion.ingest_document(document=document, db=db))

    assert db.committed_statuses == ["processing"]


def test_parsing_and_chunking_run_off_the_event_loop(ingestion, monkeypatch):
    threads = {}

    def parse(content):
        threads["parse"] = threading.get_ident()
        return [ParsedElement(element_type="paragraph", content="Text")]

    def create_chunks(elements):
        threads["chunk"] = threading.get_ident()
        return []

    monkeypatch.setattr(ingestion, "get_parser", lambda ext: parser(parse))
    monkeypatch.setattr(ingestion, "create_chunks", create_chunks)
    document = FakeDocument()

    async def run():
        threads["loop"] = threading.get_ident()
        with pytest.raises(ValueError, match="no retrieval chunks"):
            await ingestion.ingest_document(
                document=document, db=ExpiringSession(document)
            )

    asyncio.run(run())

    assert threads["parse"] != threads["loop"]
    assert threads["chunk"] != threads["loop"]


def test_only_old_processing_documents_are_stale():
    now = datetime.now(timezone.utc)
    old = now - STALE_PROCESSING_AFTER - timedelta(seconds=1)

    assert is_stale_processing("processing", old)
    assert is_stale_processing("processing", old.replace(tzinfo=None))
    assert not is_stale_processing("processing", now)
    assert not is_stale_processing("processing", None)
    assert not is_stale_processing("ready", old)


def test_document_response_exposes_stale_flag_without_timestamp():
    response = DocumentResponse(
        id=uuid4(),
        vendor_id=uuid4(),
        filename="policy.pdf",
        file_type="pdf",
        mime_type="application/pdf",
        size_bytes=10,
        status="processing",
        updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    data = response.model_dump()

    assert data["stale_processing"] is True
    assert "updated_at" not in data


def never_parse(content):
    raise AssertionError("parser must not run for an unverified file")


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {"ContentLength": len(RAW_CONTENT) + 1},
        {"ContentLength": 26 * 1024 * 1024},
    ],
)
def test_size_mismatch_fails_before_download(ingestion, monkeypatch, metadata):
    monkeypatch.setattr(ingestion, "get_object_metadata", lambda key: metadata)

    def no_download(key):
        raise AssertionError("an unverified object must not be downloaded")

    monkeypatch.setattr(ingestion, "download_object", no_download)
    monkeypatch.setattr(ingestion, "get_parser", lambda ext: parser(never_parse))
    document = FakeDocument()
    db = ExpiringSession(document)

    with pytest.raises(ValueError):
        asyncio.run(ingestion.ingest_document(document=document, db=db))

    assert document.status == "failed"


def test_replaced_content_is_rejected_by_hash(ingestion, monkeypatch):
    # Same size, different bytes: e.g. the object was overwritten after
    # the upload was confirmed.
    tampered = b"RAW BYTES"
    monkeypatch.setattr(ingestion, "download_object", lambda key: tampered)
    monkeypatch.setattr(ingestion, "get_parser", lambda ext: parser(never_parse))
    document = FakeDocument()
    db = ExpiringSession(document)

    with pytest.raises(ValueError, match="SHA-256"):
        asyncio.run(ingestion.ingest_document(document=document, db=db))

    assert document.status == "failed"
    assert "SHA-256" in document.extra_data["ingestion_error"]
