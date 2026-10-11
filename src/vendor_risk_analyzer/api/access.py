"""Company Admin controls. ZITADEL alone supplies the user's application roles."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from vendor_risk_analyzer.auth.access import resolve_access
from vendor_risk_analyzer.auth.dependencies import require_roles, verify_csrf
from vendor_risk_analyzer.db.models import TenantMembership, Vendor, VendorAssignment
from vendor_risk_analyzer.db.session import get_db

router = APIRouter(prefix="/api/admin/access", tags=["Company access"])
admin = require_roles("admin")
DB = Annotated[AsyncSession, Depends(get_db)]
AdminUser = Annotated[dict, Depends(admin)]
Csrf = Annotated[None, Depends(verify_csrf)]


class MemberAccess(BaseModel):
    access_mode: Literal["all", "assigned"]
    vendor_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    enabled: bool = True


class NewMember(MemberAccess):
    subject: str = Field(min_length=1, max_length=255, pattern=r"^\S+$")


async def save_assignments(db, access, member, payload):
    if member.subject == access.member.subject and not payload.enabled:
        raise HTTPException(400, "You cannot disable your own company access")
    vendor_ids = set(payload.vendor_ids)
    if payload.access_mode == "all" and vendor_ids:
        raise HTTPException(422, "All-vendor access does not need assignments")
    if vendor_ids:
        found = set(
            (
                await db.execute(
                    select(Vendor.id).where(
                        Vendor.tenant_id == access.tenant.id, Vendor.id.in_(vendor_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if found != vendor_ids:
            raise HTTPException(404, "One or more vendors were not found")
    # A single transaction replaces the policy and its assignments.
    member.access_mode = payload.access_mode
    member.enabled = payload.enabled
    await db.execute(
        delete(VendorAssignment).where(
            VendorAssignment.tenant_id == access.tenant.id,
            VendorAssignment.member_id == member.id,
        )
    )
    for vendor_id in vendor_ids:
        db.add(
            VendorAssignment(
                tenant_id=access.tenant.id, member_id=member.id, vendor_id=vendor_id
            )
        )
    await db.commit()
    return {"id": member.id, "subject": member.subject, **payload.model_dump()}


@router.get("/members")
async def list_members(db: DB, user: AdminUser):
    access = await resolve_access(db, user)
    members = (
        (
            await db.execute(
                select(TenantMembership)
                .where(TenantMembership.tenant_id == access.tenant.id)
                .order_by(TenantMembership.name, TenantMembership.subject)
            )
        )
        .scalars()
        .all()
    )
    assignments = (
        (
            await db.execute(
                select(VendorAssignment).where(
                    VendorAssignment.tenant_id == access.tenant.id
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "company": access.tenant.name,
        "default_access": access.tenant.default_access,
        "members": [
            {
                "id": member.id,
                "subject": member.subject,
                "name": member.name,
                "email": member.email,
                "enabled": member.enabled,
                "access_mode": member.access_mode,
                "vendor_ids": [
                    row.vendor_id for row in assignments if row.member_id == member.id
                ],
            }
            for member in members
        ],
    }


@router.post("/members", status_code=201)
async def add_member(payload: NewMember, db: DB, user: AdminUser, _: Csrf):
    access = await resolve_access(db, user)
    member = TenantMembership(
        tenant_id=access.tenant.id,
        subject=payload.subject,
        access_mode="assigned",
        enabled=True,
    )
    db.add(member)
    try:
        await db.flush()
        return await save_assignments(db, access, member, payload)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "This company member already exists") from exc


@router.put("/members/{member_id}")
async def update_member(
    member_id: UUID,
    payload: MemberAccess,
    db: DB,
    user: AdminUser,
    _: Csrf,
):
    access = await resolve_access(db, user)
    member = await db.scalar(
        select(TenantMembership)
        .where(
            TenantMembership.tenant_id == access.tenant.id,
            TenantMembership.id == member_id,
        )
        .with_for_update()
    )
    if member is None:
        raise HTTPException(404, "Company member not found")
    return await save_assignments(db, access, member, payload)


class CompanyDefaults(BaseModel):
    default_access: Literal["all", "assigned"]


@router.put("/defaults")
async def update_defaults(
    payload: CompanyDefaults,
    db: DB,
    user: AdminUser,
    _: Csrf,
):
    access = await resolve_access(db, user)
    access.tenant.default_access = payload.default_access
    await db.commit()
    return payload
