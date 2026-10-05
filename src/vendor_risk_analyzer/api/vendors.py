from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from vendor_risk_analyzer.auth.dependencies import (
    require_roles,
    verify_csrf,
)
from vendor_risk_analyzer.db.models import Vendor
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
    result = await db.execute(
        select(Vendor).order_by(
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
    vendor = Vendor(
        name=payload.name.strip(),
        website=payload.website,
        status="active",
    )

    db.add(vendor)

    try:
        await db.commit()

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Vendor already exists",
        ) from exc

    await db.refresh(vendor)

    return vendor