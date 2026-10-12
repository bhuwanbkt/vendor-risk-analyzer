"""Company audit log for admins."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from vendor_risk_analyzer.audit.service import ACTION_LABELS, list_events
from vendor_risk_analyzer.auth.access import resolve_access
from vendor_risk_analyzer.auth.dependencies import require_roles
from vendor_risk_analyzer.db.session import get_db

router = APIRouter(prefix="/api/admin/audit", tags=["Audit"])


@router.get("")
async def audit_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[dict, Depends(require_roles("admin"))],
    limit: int = Query(default=100, ge=1, le=500),
    action: str | None = Query(default=None, max_length=64),
    vendor_id: UUID | None = None,
    before: datetime | None = None,
):
    """Newest-first events for the admin's selected company only."""
    access = await resolve_access(db, user)
    return {
        "events": await list_events(
            db,
            tenant_id=access.tenant.id,
            limit=limit,
            action=action if action in ACTION_LABELS else None,
            vendor_id=vendor_id,
            before=before,
        )
    }
