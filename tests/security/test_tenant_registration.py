import asyncio
from uuid import UUID

import pytest
from sqlalchemy import select

from vendor_risk_analyzer.auth.access import LEGACY_TENANT_ID
from vendor_risk_analyzer.auth.tenant_registration import register_tenant
from vendor_risk_analyzer.db.models import Tenant, Vendor


def test_operator_preview_binding_and_rebinding_guard(security_api):
    api = security_api
    legacy = Tenant(
        id=LEGACY_TENANT_ID, name="Existing workspace", default_access="all"
    )
    api.db.session.add(legacy)
    api.db.session.commit()
    args = {"organization_id": "verified-company", "name": "My Company", "legacy": True}
    asyncio.run(register_tenant(api.db, **args))
    assert legacy.zitadel_org_id is None
    assert "commit" not in api.db.calls
    asyncio.run(register_tenant(api.db, **args, write=True))
    assert (
        legacy.zitadel_org_id == "verified-company" and legacy.default_access == "all"
    )
    asyncio.run(register_tenant(api.db, **args, write=True))  # idempotent
    with pytest.raises(ValueError, match="already bound"):
        asyncio.run(
            register_tenant(
                api.db, **{**args, "organization_id": "another-company"}, write=True
            )
        )
    assert legacy.zitadel_org_id == "verified-company"


def test_new_company_is_empty_and_defaults_to_assigned(security_api):
    api = security_api
    tenant = asyncio.run(
        register_tenant(
            api.db, organization_id="new-org", name="New Company", write=True
        )
    )
    assert tenant.default_access == "assigned"
    assert (
        api.db.session.scalar(select(Vendor.id).where(Vendor.tenant_id == tenant.id))
        is None
    )
    assert (
        api.db.session.get(Vendor, UUID(api.ids["vendor_a"])).tenant_id == api.tenant.id
    )


def test_org_cannot_claim_legacy_if_already_registered_separately(security_api):
    api = security_api
    api.db.session.add(Tenant(id=LEGACY_TENANT_ID, name="Legacy", default_access="all"))
    api.db.session.commit()
    with pytest.raises(ValueError, match="separate company"):
        asyncio.run(
            register_tenant(
                api.db,
                organization_id="test-org",
                name="Attempt",
                legacy=True,
                write=True,
            )
        )
