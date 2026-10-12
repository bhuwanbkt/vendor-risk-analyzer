"""Starting document processing is an atomic, status-checked claim."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from vendor_risk_analyzer.db.models import Document

pytestmark = pytest.mark.security


def set_status(api, status, updated_at):
    document = api.db.session.get(Document, UUID(api.ids["document_a"]))
    document.status = status
    document.updated_at = updated_at
    api.db.session.commit()


def ingest(api):
    vendor = api.ids["vendor_a"]
    document = api.ids["document_a"]
    return api.client.post(
        f"/api/vendors/{vendor}/documents/{document}/ingest",
        headers={"X-CSRF-Token": "test-csrf-token"},
    )


def test_document_already_processing_cannot_be_claimed_again(security_api):
    security_api.authenticate(["analyst"])
    set_status(security_api, "processing", datetime.now(timezone.utc))

    response = ingest(security_api)

    assert response.status_code == 409
    security_api.cloud.ingest.assert_not_called()


def test_document_being_embedded_cannot_be_reprocessed(security_api):
    security_api.authenticate(["analyst"])
    set_status(security_api, "embedding", datetime.now(timezone.utc))

    assert ingest(security_api).status_code == 409
    security_api.cloud.ingest.assert_not_called()


def test_stale_processing_document_can_be_retried(security_api):
    security_api.authenticate(["analyst"])
    set_status(
        security_api,
        "processing",
        datetime.now(timezone.utc) - timedelta(hours=1),
    )

    response = ingest(security_api)

    assert response.status_code == 200
    security_api.cloud.ingest.assert_awaited_once()
