"""Requests bind their database session to the caller's company."""

from __future__ import annotations

import pytest

from vendor_risk_analyzer.db.tenant_context import BYPASS_KEY, TENANT_KEY

pytestmark = pytest.mark.security


def test_request_session_is_bound_to_the_callers_company(security_api):
    security_api.authenticate(["viewer"])

    response = security_api.client.get("/api/vendors")

    assert response.status_code == 200
    info = security_api.db.session.info
    assert info[TENANT_KEY] == str(security_api.tenant.id)
    assert info[BYPASS_KEY] is False


def test_rejected_request_never_binds_a_company(security_api):
    response = security_api.client.get("/api/vendors")

    assert response.status_code == 401
    assert TENANT_KEY not in security_api.db.session.info


def test_system_page_reports_row_level_security_status(security_api):
    security_api.authenticate(["admin"])

    response = security_api.client.get("/admin/system")

    assert response.status_code == 200
    assert "Row-level security" in response.text
    # The test database is SQLite, so nothing is enforced here.
    assert "Not enforced: Not PostgreSQL" in response.text
