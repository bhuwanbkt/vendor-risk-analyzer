"""Operator-only company registration; never exposed through public app routes."""

from sqlalchemy import select

from vendor_risk_analyzer.auth.access import LEGACY_TENANT_ID
from vendor_risk_analyzer.db.models import Tenant


async def register_tenant(
    db, *, organization_id, name, legacy=False, default_access=None, write=False
):
    if (
        not isinstance(organization_id, str)
        or not 0 < len(organization_id) <= 255
        or organization_id.strip() != organization_id
    ):
        raise ValueError("A non-empty ZITADEL Organization ID is required")
    if not isinstance(name, str) or not 0 < len(name.strip()) <= 255:
        raise ValueError("A company name is required")
    if default_access not in (None, "all", "assigned"):
        raise ValueError("Default access must be all or assigned")
    existing = await db.scalar(
        select(Tenant).where(Tenant.zitadel_org_id == organization_id)
    )
    if legacy:
        tenant = await db.scalar(select(Tenant).where(Tenant.id == LEGACY_TENANT_ID))
        if tenant is None:
            raise ValueError("Run the company migration before binding existing data")
        if tenant.zitadel_org_id not in (None, organization_id):
            raise ValueError(
                "Existing data is already bound to a different organization"
            )
        if existing is not None and existing.id != tenant.id:
            raise ValueError("This organization already has a separate company")
    else:
        tenant = existing or Tenant(default_access=default_access or "assigned")
    if write:
        tenant.zitadel_org_id = organization_id
        tenant.name = name.strip()
        if default_access is not None:
            tenant.default_access = default_access
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)
    return tenant
