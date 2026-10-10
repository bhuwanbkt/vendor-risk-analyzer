from __future__ import annotations

from uuid import UUID

import pytest

pytestmark = pytest.mark.security


@pytest.mark.parametrize("role", ["viewer", "analyst", "admin"])
def test_document_listing_contains_only_requested_vendor(security_api, role):
    security_api.authenticate([role])
    for suffix in ("a", "b"):
        response = security_api.client.get(
            f"/api/vendors/{security_api.ids[f'vendor_{suffix}']}/documents"
        )
        assert response.status_code == 200
        assert [document["id"] for document in response.json()] == [
            security_api.ids[f"document_{suffix}"]
        ]
        assert all(
            document["vendor_id"] == security_api.ids[f"vendor_{suffix}"]
            for document in response.json()
        )


@pytest.mark.parametrize("action", ["complete", "ingest"])
@pytest.mark.parametrize("role", ["analyst", "admin"])
def test_document_from_another_vendor_cannot_be_modified(security_api, action, role):
    security_api.authenticate([role])
    ids = security_api.ids
    response = security_api.client.post(
        f"/api/vendors/{ids['vendor_a']}/documents/{ids['document_b']}/{action}",
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"
    assert security_api.db.calls == ["execute"]
    missing = security_api.client.post(
        f"/api/vendors/{ids['vendor_a']}/documents/{ids['missing']}/{action}",
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert missing.status_code == response.status_code
    assert missing.json() == response.json()
    assert security_api.db.calls == ["execute", "execute"]
    security_api.cloud.object_metadata.assert_not_called()
    security_api.cloud.ingest.assert_not_called()


@pytest.mark.parametrize("role", ["viewer", "analyst", "admin"])
def test_assessment_api_and_page_do_not_mix_findings(security_api, role):
    security_api.authenticate([role])
    ids = security_api.ids
    response = security_api.client.get(f"/api/assessments/{ids['assessment_a']}")
    assert response.status_code == 200
    assert response.json()["vendor_id"] == ids["vendor_a"]
    assert [finding["title"] for finding in response.json()["findings"]] == [
        "Private finding A"
    ]
    assert "Private finding B" not in response.text
    page = security_api.client.get(f"/assessments/{ids['assessment_a']}")
    assert page.status_code == 200
    assert "Private finding A" in page.text
    assert "Private finding B" not in page.text
    assert "Private summary B" not in page.text


def test_upload_key_and_document_use_the_requested_vendor(security_api):
    security_api.authenticate(["analyst"])
    vendor = security_api.ids["vendor_b"]
    response = security_api.client.post(
        f"/api/vendors/{vendor}/documents/upload-url",
        json={
            "filename": "policy.txt",
            "content_type": "text/plain",
            "size_bytes": 5,
            "sha256": "c" * 64,
        },
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert response.status_code == 200
    assert response.json()["object_key"].startswith(f"vendors/{vendor}/raw/")
    listing = security_api.client.get(f"/api/vendors/{vendor}/documents").json()
    created = next(
        document
        for document in listing
        if document["id"] == response.json()["document_id"]
    )
    assert created["vendor_id"] == vendor


def test_chat_forwards_explicit_vendor_context(security_api):
    security_api.authenticate(["analyst"])
    vendor = security_api.ids["vendor_b"]
    response = security_api.client.post(
        "/api/chat",
        json={"vendor_id": vendor, "question": "What evidence is available?"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert response.status_code == 200
    assert response.json()["vendor_id"] == vendor
    assert security_api.cloud.chat_factory.return_value.answer.await_args.kwargs[
        "vendor_id"
    ] == UUID(vendor)


def test_chat_does_not_infer_vendor_from_question(security_api):
    security_api.authenticate(["analyst"])
    response = security_api.client.post(
        "/api/chat",
        json={"question": "Analyze Vendor A"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert response.status_code == 422
    security_api.assert_no_work()
