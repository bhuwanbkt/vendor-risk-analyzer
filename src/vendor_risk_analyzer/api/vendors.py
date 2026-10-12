from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import uuid4
from vendor_risk_analyzer.audit.service import record as audit
from vendor_risk_analyzer.auth.access import resolve_access

from vendor_risk_analyzer.auth.dependencies import (
    require_roles,
    verify_csrf,
)
from vendor_risk_analyzer.db.models import Vendor, VendorAssignment
from vendor_risk_analyzer.db.session import get_db
from vendor_risk_analyzer.schemas.vendor import (
    VendorCreate,
    VendorResponse,
)


router = APIRouter(
    prefix="/api/vendors",
    tags=["Vendors"],
)


@router.get(
    "",
    response_model=list[VendorResponse],
)
async def list_vendors(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(
        require_roles(
            "viewer",
            "analyst",
            "admin",
        )
    ),
):
    access = await resolve_access(db, user)
    result = await db.execute(
        select(Vendor).where(access.vendor_filter()).order_by(
            Vendor.name
        )
    )

    return result.scalars().all()


@router.post(
    "",
    response_model=VendorResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_vendor(
    payload: VendorCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(
        require_roles(
            "analyst",
            "admin",
        )
    ),
    _: None = Depends(verify_csrf),
):
    access = await resolve_access(db, user)
    vendor = Vendor(
        id=uuid4(),
        tenant_id=access.tenant.id,
        name=payload.name.strip(),
        website=payload.website,
        status="active",
    )

    db.add(vendor)

    try:
        await db.flush()
        if "admin" not in access.roles and access.member.access_mode == "assigned":
            db.add(VendorAssignment(tenant_id=access.tenant.id, member_id=access.member.id, vendor_id=vendor.id))
        await db.commit()

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Vendor already exists",
        ) from exc

    await db.refresh(vendor)

    await audit(
        access,
        user,
        "vendor.created",
        target_type="vendor",
        target_id=vendor.id,
        vendor_id=vendor.id,
        details={"name": vendor.name},
    )

    return vendor
