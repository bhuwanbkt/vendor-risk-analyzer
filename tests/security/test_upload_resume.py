"""Retrying an upload that was never completed."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.security


def request_upload(api, vendor, sha256, size=5):
    return api.client.post(
        f"/api/vendors/{vendor}/documents/upload-url",
        json={
            "filename": "policy.txt",
            "content_type": "text/plain",
            "size_bytes": size,
            "sha256": sha256,
        },
        headers={"X-CSRF-Token": "test-csrf-token"},
    )


def test_abandoned_upload_can_be_retried(security_api):
    security_api.authenticate(["analyst"])
    vendor = security_api.ids["vendor_a"]

    first = request_upload(security_api, vendor, "e" * 64)
    second = request_upload(security_api, vendor, "e" * 64)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["document_id"] == first.json()["document_id"]
    assert second.json()["object_key"] == first.json()["object_key"]
    assert security_api.cloud.upload_url.call_count == 2


def test_completed_upload_is_still_a_duplicate(security_api):
    security_api.authenticate(["analyst"])
    vendor = security_api.ids["vendor_a"]

    # The seeded document for vendor A has status "uploaded".
    response = request_upload(security_api, vendor, "a" * 64)

    assert response.status_code == 409


def test_pending_upload_with_different_size_is_rejected(security_api):
    security_api.authenticate(["analyst"])
    vendor = security_api.ids["vendor_a"]

    assert request_upload(security_api, vendor, "f" * 64).status_code == 200
    assert request_upload(security_api, vendor, "f" * 64, size=6).status_code == 409
