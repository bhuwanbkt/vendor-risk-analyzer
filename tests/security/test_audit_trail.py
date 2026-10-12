"""Company audit trail: what is recorded, who can read it, and isolation."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from vendor_risk_analyzer.audit import service as audit_service
from vendor_risk_analyzer.db.models import AuditEvent, Tenant, TenantMembership

pytestmark = pytest.mark.security

CSRF = {"X-CSRF-Token": "test-csrf-token"}


def actions(api):
    return [event.action for event in api.audit_events]


def test_vendor_creation_is_audited_with_actor_and_company(security_api):
    security_api.authenticate(["analyst"])

    response = security_api.client.post("/api/vendors", json={"name": "Audited Vendor"}, headers=CSRF)

    assert response.status_code == 201
    event = security_api.audit_events[-1]
    assert event.action == "vendor.created"
    assert event.actor_subject == "test-user"
    assert event.actor_name == "Test User"
    assert event.tenant_id == security_api.tenant.id
    assert event.vendor_id == UUID(response.json()["id"])
    assert event.details == {"name": "Audited Vendor"}


def test_chat_audit_never_contains_the_question(security_api):
    security_api.authenticate(["analyst"])
    question = "What is the secret incident timeline?"

    security_api.client.post(
        "/api/chat",
        json={"vendor_id": security_api.ids["vendor_a"], "question": question},
        headers=CSRF,
    )

    event = security_api.audit_events[-1]
    assert event.action == "chat.question_asked"
    assert event.details == {"question_length": len(question), "via": "web"}
    assert "secret" not in str(event.details)


def test_assessment_request_and_export_are_audited(security_api):
    security_api.authenticate(["analyst"])
    security_api.client.post(
        f"/api/vendors/{security_api.ids['vendor_a']}/assessments", headers=CSRF
    )
    security_api.client.get(
        f"/api/assessments/{security_api.ids['assessment_a']}/export?format=docx"
    )

    assert actions(security_api)[-2:] == ["assessment.requested", "assessment.exported"]
    assert security_api.audit_events[-1].details == {"format": "docx"}


def test_access_changes_record_previous_values(security_api):
    security_api.authenticate(["admin"])
    member = security_api.db.session.query(TenantMembership).filter_by(subject="api-user").one()
    previous_mode = member.access_mode

    response = security_api.client.put(
        f"/api/admin/access/members/{member.id}",
        json={"access_mode": "assigned", "vendor_ids": [security_api.ids["vendor_a"]]},
        headers=CSRF,
    )

    assert response.status_code == 200
    event = security_api.audit_events[-1]
    assert event.action == "access.member_updated"
    assert event.details["previous"]["access_mode"] == previous_mode
    assert event.details["access_mode"] == "assigned"
    assert event.details["vendor_ids"] == [security_api.ids["vendor_a"]]


def test_rejected_requests_record_nothing(security_api):
    security_api.authenticate(["viewer"])

    assert security_api.client.post("/api/vendors", json={"name": "Nope"}, headers=CSRF).status_code == 403
    assert security_api.audit_events == []


def test_audit_failure_never_blocks_the_action(security_api, monkeypatch):
    def broken_factory():
        raise RuntimeError("audit database unavailable")

    monkeypatch.setattr(audit_service, "session_factory", broken_factory)
    security_api.authenticate(["analyst"])

    response = security_api.client.post("/api/vendors", json={"name": "Still Created"}, headers=CSRF)

    assert response.status_code == 201


def seed_events(api):
    other = Tenant(id=uuid4(), name="Other company", zitadel_org_id="other-org-audit", default_access="all")
    api.db.session.add(other)
    api.db.session.flush()
    api.db.session.add_all([
        AuditEvent(tenant_id=api.tenant.id, actor_subject="test-user", actor_name="<b>Test</b>",
                   action="vendor.created", target_type="vendor", details={"name": "Mine"}),
        AuditEvent(tenant_id=other.id, actor_subject="someone", actor_name="Other",
                   action="vendor.created", target_type="vendor", details={"name": "Theirs"}),
    ])
    api.db.session.commit()


def test_admin_reads_only_their_company_events(security_api):
    seed_events(security_api)
    security_api.authenticate(["admin"])

    response = security_api.client.get("/api/admin/audit")

    assert response.status_code == 200
    names = [event["details"].get("name") for event in response.json()["events"]]
    assert names == ["Mine"]

    page = security_api.client.get("/admin/audit")
    assert page.status_code == 200
    assert "Mine" in page.text and "Theirs" not in page.text
    assert "&lt;b&gt;Test&lt;/b&gt;" in page.text


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_non_admins_cannot_read_the_audit_log(security_api, role):
    security_api.authenticate([role])

    assert security_api.client.get("/api/admin/audit").status_code == 403
    assert security_api.client.get("/admin/audit", follow_redirects=False).status_code in (302, 303, 403)
