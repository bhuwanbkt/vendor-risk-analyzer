"""Exercise company and user assignments through the real HTTP boundaries."""

from uuid import UUID

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from vendor_risk_analyzer.db.models import (
    Tenant,
    TenantMembership,
    Vendor,
    VendorAssignment,
)

pytestmark = pytest.mark.security
CSRF = {"X-CSRF-Token": "test-csrf-token"}


def no_cloud(api):
    for mock in vars(api.cloud).values():
        mock.assert_not_called()


@pytest.mark.parametrize("role", ["viewer", "analyst"])
@pytest.mark.parametrize(
    "path", ["/dashboard", "/vendors", "/documents", "/assessments"]
)
def test_pages_hide_foreign_or_unassigned_vendor(
    security_api, restricted_access, role, path
):
    api = security_api
    api.authenticate([role])
    response = api.client.get(path)
    assert response.status_code == 200
    assert "Vendor A" in response.text
    for private in (
        "Vendor B",
        "Private finding B",
        "Private summary B",
        api.ids["vendor_b"],
        api.ids["assessment_b"],
    ):
        assert private not in response.text
    if path == "/dashboard":
        from html.parser import HTMLParser

        class Metrics(HTMLParser):
            def __init__(self):
                super().__init__()
                self.inside = False
                self.values = []

            def handle_starttag(self, tag, attrs):
                if tag == "strong":
                    self.inside = True

            def handle_endtag(self, tag):
                if tag == "strong":
                    self.inside = False

            def handle_data(self, data):
                if self.inside and data.strip().isdigit():
                    self.values.append(int(data))

        metrics = Metrics()
        metrics.feed(response.text)
        assert metrics.values[:3] == [1, 1, 1]
    no_cloud(api)


def test_lists_and_reports_apply_assignment_and_company_scope(
    security_api, restricted_access
):
    api = security_api
    api.authenticate(["analyst"])
    assert [row["id"] for row in api.client.get("/api/vendors").json()] == [
        api.ids["vendor_a"]
    ]
    assert (
        api.client.get(f"/api/vendors/{api.ids['vendor_a']}/documents").status_code
        == 200
    )
    for path in (
        f"/api/vendors/{api.ids['vendor_b']}/documents",
        f"/api/assessments/{api.ids['assessment_b']}",
        f"/assessments/{api.ids['assessment_b']}",
    ):
        response = api.client.get(path)
        assert response.status_code == 404
        assert "Private" not in response.text
    no_cloud(api)


@pytest.mark.parametrize(
    "operation", ["upload", "complete", "ingest", "assessment", "chat"]
)
def test_blocked_vendor_is_rejected_before_cloud_or_writes(
    security_api, restricted_access, operation
):
    api = security_api
    api.authenticate(["analyst"])
    vendor = api.ids["vendor_b"]
    path = f"/api/vendors/{vendor}"
    body = None
    if operation == "upload":
        path += "/documents/upload-url"
        body = {
            "filename": "secret.txt",
            "content_type": "text/plain",
            "size_bytes": 5,
            "sha256": "d" * 64,
        }
    elif operation in ("complete", "ingest"):
        path += f"/documents/{api.ids['document_b']}/{operation}"
    elif operation == "assessment":
        path += "/assessments"
    else:
        path = "/api/chat"
        body = {"vendor_id": vendor, "question": "Reveal another vendor"}
    response = api.client.post(path, json=body, headers=CSRF)
    assert response.status_code == 404
    assert "commit" not in api.db.calls and "add" not in api.db.calls
    no_cloud(api)


def test_admin_has_company_scope_even_with_all_vendor_access(
    security_api, restricted_access
):
    api = security_api
    api.authenticate(["admin"])
    response = api.client.get("/api/vendors")
    expected = (
        [api.ids["vendor_a"]]
        if restricted_access == "company"
        else [api.ids["vendor_a"], api.ids["vendor_b"]]
    )
    assert [row["id"] for row in response.json()] == expected


def test_assigned_analyst_gets_ownership_of_new_vendor(security_api, restricted_access):
    api = security_api
    member = api.db.session.scalar(
        select(TenantMembership).where(TenantMembership.subject == "test-user")
    )
    member.access_mode = "assigned"
    api.db.session.commit()
    api.authenticate(["analyst"])
    created = api.client.post(
        "/api/vendors", json={"name": "New restricted vendor"}, headers=CSRF
    )
    assert created.status_code == 201
    vendor_id = created.json()["id"]
    vendor = api.db.session.get(Vendor, UUID(vendor_id))
    assert vendor.tenant_id == api.tenant.id
    assert vendor_id in [row["id"] for row in api.client.get("/api/vendors").json()]
    assert api.client.get(f"/api/vendors/{vendor_id}/documents").status_code == 200


def test_signed_bearer_obeys_assignments(security_api, bearer_api, restricted_access):
    api = security_api
    headers = bearer_api.headers(roles=["analyst"])
    assert [
        row["id"] for row in api.client.get("/api/vendors", headers=headers).json()
    ] == [api.ids["vendor_a"]]
    assert (
        api.client.post(
            "/api/chat",
            headers=headers,
            json={"vendor_id": api.ids["vendor_b"], "question": "Foreign evidence"},
        ).status_code
        == 404
    )
    no_cloud(api)


def test_vendor_names_are_unique_within_each_company(
    security_api, bearer_api, restricted_access
):
    api = security_api
    primary = bearer_api.headers(roles=["admin"])
    claim = f"urn:zitadel:iam:org:project:{bearer_api.project}:roles"
    other = bearer_api.headers(
        updates={claim: {"admin": {"other-org": "other.invalid"}}}
    )
    assert (
        api.client.post(
            "/api/vendors", headers=primary, json={"name": "Vendor A"}
        ).status_code
        == 409
    )
    assert (
        api.client.post(
            "/api/vendors", headers=other, json={"name": "Vendor A"}
        ).status_code
        == 201
    )
    assert api.client.get("/api/vendors", headers=other).status_code == 200
    assert (
        api.client.get(
            f"/api/vendors/{api.ids['vendor_a']}/documents", headers=other
        ).status_code
        == 404
    )


def test_multi_company_admin_role_is_not_combined_with_viewer(
    security_api, bearer_api, restricted_access
):
    api = security_api
    claim = f"urn:zitadel:iam:org:project:{bearer_api.project}:roles"
    headers = bearer_api.headers(
        updates={
            claim: {
                "viewer": {"test-org": "example.invalid"},
                "admin": {"other-org": "other.invalid"},
            }
        }
    )
    assert api.client.get("/api/vendors", headers=headers).status_code == 403
    assert (
        api.client.post(
            "/api/vendors",
            headers={**headers, "X-Organization-ID": "test-org"},
            json={"name": "Escalate"},
        ).status_code
        == 403
    )
    api.assert_no_work()
    me = api.client.get(
        "/auth/me", headers={**headers, "X-Organization-ID": "test-org"}
    ).json()
    assert me["roles"] == ["viewer"]
    assert (
        api.client.get(
            "/api/vendors", headers={**headers, "X-Organization-ID": "ungranted-org"}
        ).status_code
        == 403
    )
    assert (
        api.client.post(
            "/api/vendors",
            headers={**headers, "X-Organization-ID": "other-org"},
            json={"name": "Other company vendor"},
        ).status_code
        == 201
    )


def test_old_flattened_cookie_cannot_access_existing_workspace(security_api):
    api = security_api
    api.authenticate(user={"sub": "test-user", "roles": ["admin"]})
    assert api.client.get("/api/vendors").status_code == 403
    page = api.client.get("/dashboard")
    assert page.status_code == 403 and "Your account needs access" in page.text
    assert "Sign in again to refresh your company access" in page.text
    api.assert_no_work()


def test_unknown_verified_org_cannot_claim_legacy_data(security_api, bearer_api):
    api = security_api
    api.tenant.zitadel_org_id = None
    api.db.session.commit()
    headers = bearer_api.headers(roles=["admin"])
    response = api.client.get("/api/vendors", headers=headers)
    assert response.status_code == 403
    assert api.db.session.get(Tenant, api.tenant.id).zitadel_org_id is None
    assert "commit" not in api.db.calls
    no_cloud(api)


def test_company_switch_requires_grant_and_csrf(security_api, restricted_access):
    api = security_api
    api.authenticate(
        user={
            "sub": "test-user",
            "organization_roles": {
                "test-org": ["viewer"],
                "other-org": ["admin"],
            },
        }
    )
    assert api.client.get("/").headers["location"] == "/companies"
    assert api.client.get("/dashboard").headers["location"] == "/companies"
    assert (
        api.client.post(
            "/auth/company", json={"organization_id": "test-org"}
        ).status_code
        == 403
    )
    assert (
        api.client.post(
            "/auth/company", json={"organization_id": "evil-org"}, headers=CSRF
        ).status_code
        == 403
    )
    api.assert_no_work()
    companies = api.client.get("/companies")
    assert "Test Company" in companies.text and "Other Company" in companies.text
    assert "Vendor A" not in companies.text
    assert (
        api.client.post(
            "/auth/company", json={"organization_id": "test-org"}, headers=CSRF
        ).status_code
        == 200
    )
    assert api.client.get("/auth/me").json()["roles"] == ["viewer"]
    assert api.client.get("/admin/access").status_code == 403
    assert (
        api.client.post(
            "/auth/company", json={"organization_id": "other-org"}, headers=CSRF
        ).status_code
        == 403
    )  # token rotated


def test_admin_restricts_user_and_revokes_assignments_without_new_login(security_api):
    api = security_api
    api.authenticate(["admin"])
    assert api.client.get("/admin/access").status_code == 200
    members = api.client.get("/api/admin/access/members").json()["members"]
    target = next(row for row in members if row["subject"] == "api-user")
    changed = api.client.put(
        f"/api/admin/access/members/{target['id']}",
        headers=CSRF,
        json={"access_mode": "assigned", "vendor_ids": [api.ids["vendor_a"]]},
    )
    assert changed.status_code == 200
    api.authenticate(
        user={"sub": "api-user", "organization_roles": {"test-org": ["viewer"]}}
    )
    assert [row["id"] for row in api.client.get("/api/vendors").json()] == [
        api.ids["vendor_a"]
    ]
    # Remove the assignment while the reader's same signed cookie remains active.
    api.db.session.execute(
        delete(VendorAssignment).where(VendorAssignment.member_id == UUID(target["id"]))
    )
    api.db.session.commit()
    assert api.client.get("/api/vendors").json() == []
    assert (
        api.client.get(f"/api/assessments/{api.ids['assessment_a']}").status_code == 404
    )
    no_cloud(api)


def test_admin_cannot_assign_foreign_vendor_or_edit_foreign_member(
    security_api, restricted_access
):
    api = security_api
    if restricted_access != "company":
        api.db.session.get(Vendor, UUID(api.ids["vendor_b"])).tenant_id = UUID(int=200)
        api.db.session.commit()
    foreign = TenantMembership(
        tenant_id=UUID(int=200), subject="foreign-user", access_mode="all", enabled=True
    )
    api.db.session.add(foreign)
    api.db.session.commit()
    api.authenticate(["admin"])
    member = api.db.session.scalar(
        select(TenantMembership).where(TenantMembership.subject == "api-user")
    )
    response = api.client.put(
        f"/api/admin/access/members/{member.id}",
        headers=CSRF,
        json={"access_mode": "assigned", "vendor_ids": [api.ids["vendor_b"]]},
    )
    assert response.status_code == 404
    assert member.access_mode == (
        "all" if restricted_access == "company" else "assigned"
    )
    assert (
        api.client.put(
            f"/api/admin/access/members/{foreign.id}",
            headers=CSRF,
            json={"access_mode": "all"},
        ).status_code
        == 404
    )
    assert "foreign-user" not in api.client.get("/api/admin/access/members").text


@pytest.mark.parametrize("role", ["viewer", "analyst"])
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/admin/access/members", None),
        (
            "POST",
            "/api/admin/access/members",
            {"subject": "new-user", "access_mode": "all"},
        ),
        ("PUT", "/api/admin/access/defaults", {"default_access": "all"}),
    ],
)
def test_only_company_admin_can_manage_access(security_api, role, method, path, body):
    api = security_api
    api.authenticate([role])
    assert api.client.request(method, path, json=body, headers=CSRF).status_code == 403
    api.assert_no_work()


def test_member_registration_requires_csrf_and_does_not_grant_roles(security_api):
    api = security_api
    api.authenticate(["admin"])
    body = {"subject": "new-user", "access_mode": "all"}
    assert api.client.post("/api/admin/access/members", json=body).status_code == 403
    api.assert_no_work()
    assert (
        api.client.post(
            "/api/admin/access/members", json=body, headers=CSRF
        ).status_code
        == 201
    )
    assert (
        api.client.post(
            "/api/admin/access/members", json=body, headers=CSRF
        ).status_code
        == 409
    )
    api.authenticate(user={"sub": "new-user", "organization_roles": {}})
    assert api.client.get("/api/vendors").status_code == 403


def test_disabled_member_cannot_read_even_with_admin_role(security_api):
    api = security_api
    member = api.db.session.scalar(
        select(TenantMembership).where(TenantMembership.subject == "test-user")
    )
    member.enabled = False
    api.db.session.commit()
    api.authenticate(["admin"])
    assert api.client.get("/api/vendors").status_code == 403
    page = api.client.get("/dashboard")
    assert page.status_code == 403 and "company access has been disabled" in page.text
    assert "Vendor A" not in page.text
    no_cloud(api)


def test_new_company_member_uses_restricted_default_and_admin_controls(security_api):
    api = security_api
    api.tenant.default_access = "assigned"
    api.db.session.commit()
    api.authenticate(
        user={
            "sub": "first-time-viewer",
            "organization_roles": {"test-org": ["viewer"]},
        }
    )
    assert api.client.get("/api/vendors").json() == []
    member = api.db.session.scalar(
        select(TenantMembership).where(TenantMembership.subject == "first-time-viewer")
    )
    assert member.access_mode == "assigned"
    api.authenticate(["admin"])
    own = api.db.session.scalar(
        select(TenantMembership).where(TenantMembership.subject == "test-user")
    )
    assert (
        api.client.put(
            f"/api/admin/access/members/{own.id}",
            headers=CSRF,
            json={"access_mode": "all", "enabled": False},
        ).status_code
        == 400
    )
    assert own.enabled is True
    assert (
        api.client.put(
            "/api/admin/access/defaults", headers=CSRF, json={"default_access": "all"}
        ).status_code
        == 200
    )
    assert member.access_mode == "assigned"  # existing policies do not change
    api.authenticate(
        user={"sub": "future-viewer", "organization_roles": {"test-org": ["viewer"]}}
    )
    assert len(api.client.get("/api/vendors").json()) == 2


def test_assignment_foreign_keys_enforce_same_company(security_api, restricted_access):
    api = security_api
    member = api.db.session.scalar(
        select(TenantMembership).where(TenantMembership.subject == "test-user")
    )
    api.db.session.add(
        VendorAssignment(
            tenant_id=UUID(int=200),
            member_id=member.id,
            vendor_id=UUID(api.ids["vendor_b"]),
        )
    )
    with pytest.raises(IntegrityError):
        api.db.session.commit()
    api.db.session.rollback()
