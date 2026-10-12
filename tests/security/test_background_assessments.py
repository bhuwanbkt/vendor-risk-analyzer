"""Creating an assessment queues it instead of running it in the request."""

from __future__ import annotations

from uuid import UUID

import pytest

from vendor_risk_analyzer.db.models import Assessment

pytestmark = pytest.mark.security


def test_create_assessment_queues_without_calling_gemini(security_api, monkeypatch):
    # Imported after the fixture has configured test settings.
    from vendor_risk_analyzer.api import assessments as assessments_api

    notified = []
    monkeypatch.setattr(
        assessments_api, "notify_new_assessment", lambda: notified.append(True)
    )
    security_api.authenticate(["analyst"])
    vendor = security_api.ids["vendor_a"]

    response = security_api.client.post(
        f"/api/vendors/{vendor}/assessments",
        headers={"X-CSRF-Token": "test-csrf-token"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["vendor_id"] == vendor
    assert body["findings"] == []
    assert body["metadata"]["progress"]["stage"] == "queued"
    assert body["metadata"]["requested_by"] == "test-user"
    assert notified == [True]

    saved = security_api.db.session.get(Assessment, UUID(body["id"]))
    assert saved.status == "queued"
    security_api.cloud.embedding_factory.assert_not_called()


def test_queued_assessment_page_shows_live_progress(security_api):
    security_api.authenticate(["analyst"])
    vendor = security_api.ids["vendor_a"]
    created = security_api.client.post(
        f"/api/vendors/{vendor}/assessments",
        headers={"X-CSRF-Token": "test-csrf-token"},
    ).json()

    page = security_api.client.get(f"/assessments/{created['id']}")

    assert page.status_code == 200
    assert 'id="assessment-progress"' in page.text
    assert "/static/js/assessment_detail.js" in page.text
    assert "What needs attention" not in page.text


def test_completed_assessment_page_has_no_progress_panel(security_api):
    security_api.authenticate(["analyst"])

    page = security_api.client.get(f"/assessments/{security_api.ids['assessment_a']}")

    assert page.status_code == 200
    assert 'id="assessment-progress"' not in page.text
    assert "assessment_detail.js" not in page.text
