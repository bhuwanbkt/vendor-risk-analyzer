"""Company and vendor authorization shared by browser, API, and MCP boundaries."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from vendor_risk_analyzer.auth.organizations import scoped_identity
from vendor_risk_analyzer.db.models import (
    Tenant,
    TenantMembership,
    Vendor,
    VendorAssignment,
)

LEGACY_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@dataclass(frozen=True)
class AccessContext:
    tenant: Tenant
    member: TenantMembership
    roles: frozenset[str]

    def vendor_filter(self):
        condition = Vendor.tenant_id == self.tenant.id
        if "admin" not in self.roles and self.member.access_mode != "all":
            condition &= Vendor.id.in_(
                select(VendorAssignment.vendor_id).where(
                    VendorAssignment.tenant_id == self.tenant.id,
                    VendorAssignment.member_id == self.member.id,
                )
            )
        return condition

    def vendor_ids(self):
        return select(Vendor.id).where(self.vendor_filter())


async def resolve_access(db, user: dict) -> AccessContext:
    user = scoped_identity(user)
    org = user["organization_id"]
    if org is None or not user["roles"]:
        raise HTTPException(403, "Select a company with an application role")
    tenant = await db.scalar(select(Tenant).where(Tenant.zitadel_org_id == org))
    if tenant is None:
        raise HTTPException(403, "Your company has not been configured for this app")
    query = select(TenantMembership).where(
        TenantMembership.tenant_id == tenant.id, TenantMembership.subject == user["sub"]
    )
    member = await db.scalar(query)
    if member is None:
        member = TenantMembership(
            tenant_id=tenant.id,
            subject=user["sub"],
            access_mode=tenant.default_access,
            name=user.get("name", "")[:255]
            if isinstance(user.get("name"), str)
            else None,
            email=user.get("email", "")[:320]
            if isinstance(user.get("email"), str)
            else None,
            enabled=True,
        )
        db.add(member)
        try:
            await db.commit()
            await db.refresh(member)
        except IntegrityError:
            # Simultaneous first requests may register the same verified subject.
            await db.rollback()
            # Rollback expires loaded ORM objects even with expire_on_commit=False.
            # Reload explicitly rather than causing an async lazy load later.
            await db.refresh(tenant)
            member = await db.scalar(query)
            if member is None:
                raise
    if not member.enabled:
        raise HTTPException(403, "Your company access has been disabled")
    return AccessContext(tenant, member, frozenset(user["roles"]))


async def require_vendor_access(db, user: dict, vendor_id: UUID) -> AccessContext:
    access = await resolve_access(db, user)
    vendor = await db.scalar(
        select(Vendor.id).where(Vendor.id == vendor_id, access.vendor_filter())
    )
    if vendor is None:
        # Identical response for unknown, foreign, or unassigned IDs.
        raise HTTPException(404, "Vendor not found")
    return access
