"""Record audit events: who did what, in which company.

Writes are best-effort. Each event uses its own short session after the
action has succeeded, so a failed audit write is logged loudly but never
undoes or blocks the user's action. Event details never include document
contents, chat questions, or answers.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from vendor_risk_analyzer.db.models import AuditEvent
from vendor_risk_analyzer.db.tenant_context import bind_system, bind_tenant

logger = logging.getLogger("uvicorn.error")


def default_session_factory():
    # Imported lazily so this module has no database side effects on import.
    from vendor_risk_analyzer.db.session import AsyncSessionLocal

    return AsyncSessionLocal()


# Replaceable in tests.
session_factory = default_session_factory


def actor_name(user: dict | None, access=None) -> str | None:
    for value in (
        (user or {}).get("name"),
        (user or {}).get("email"),
        getattr(getattr(access, "member", None), "name", None),
        getattr(getattr(access, "member", None), "email", None),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()[:320]
    return None


async def record_event(
    *,
    tenant_id: UUID | None,
    actor_subject: str | None,
    actor: str | None,
    action: str,
    target_type: str,
    target_id: Any = None,
    vendor_id: UUID | str | None = None,
    details: dict[str, Any] | None = None,
    factory=None,
) -> None:
    try:
        async with (factory or session_factory)() as db:
            if tenant_id is not None:
                await bind_tenant(db, tenant_id)
            else:
                await bind_system(db)
            db.add(
                AuditEvent(
                    tenant_id=tenant_id,
                    actor_subject=actor_subject,
                    actor_name=actor,
                    action=action,
                    target_type=target_type,
                    target_id=str(target_id) if target_id is not None else None,
                    vendor_id=UUID(str(vendor_id)) if vendor_id else None,
                    details=details or {},
                )
            )
            await db.commit()
    except Exception:
        logger.exception(
            "Audit event could not be recorded. action=%s target=%s:%s",
            action,
            target_type,
            target_id,
        )


async def record(
    access,
    user: dict | None,
    action: str,
    *,
    target_type: str,
    target_id: Any = None,
    vendor_id: UUID | str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Record an event for the current request's company member."""
    await record_event(
        tenant_id=access.tenant.id,
        actor_subject=access.member.subject,
        actor=actor_name(user, access),
        action=action,
        target_type=target_type,
        target_id=target_id,
        vendor_id=vendor_id,
        details=details,
    )


ACTION_LABELS = {
    "vendor.created": "Created vendor",
    "document.upload_requested": "Started document upload",
    "document.uploaded": "Uploaded document",
    "document.processing_started": "Started document processing",
    "assessment.requested": "Requested assessment",
    "assessment.completed": "Assessment completed",
    "assessment.failed": "Assessment failed",
    "assessment.exported": "Exported assessment",
    "chat.question_asked": "Asked AI chat",
    "chat.conversation_deleted": "Deleted chat conversation",
    "access.member_added": "Added company user",
    "access.member_updated": "Changed user access",
    "access.default_changed": "Changed default access",
    "company.selected": "Switched company",
}


async def list_events(
    db,
    *,
    tenant_id: UUID,
    limit: int = 100,
    action: str | None = None,
    vendor_id: UUID | None = None,
    before=None,
) -> list[dict[str, Any]]:
    """Newest-first events for one company, with vendor names."""
    from sqlalchemy import select

    from vendor_risk_analyzer.db.models import Vendor

    query = (
        select(AuditEvent, Vendor.name)
        .outerjoin(Vendor, Vendor.id == AuditEvent.vendor_id)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
    )
    if action:
        query = query.where(AuditEvent.action == action)
    if vendor_id:
        query = query.where(AuditEvent.vendor_id == vendor_id)
    if before is not None:
        query = query.where(AuditEvent.created_at < before)

    rows = (await db.execute(query)).all()
    return [
        {
            "id": str(event.id),
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "actor_subject": event.actor_subject,
            "actor_name": event.actor_name,
            "action": event.action,
            "action_label": ACTION_LABELS.get(event.action, event.action),
            "target_type": event.target_type,
            "target_id": event.target_id,
            "vendor_id": str(event.vendor_id) if event.vendor_id else None,
            "vendor_name": vendor_name,
            "details": event.details or {},
        }
        for event, vendor_name in rows
    ]
