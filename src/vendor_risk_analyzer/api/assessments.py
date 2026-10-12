from __future__ import annotations

import logging
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from vendor_risk_analyzer.rate_limit import rate_limit
from vendor_risk_analyzer.assessments import repository
from vendor_risk_analyzer.assessments.service import ASSESSMENT_TYPE
from vendor_risk_analyzer.assessments.worker import notify_new_assessment
from vendor_risk_analyzer.db.models import Assessment
from vendor_risk_analyzer.auth.dependencies import (
    require_roles,
    verify_csrf,
)
from vendor_risk_analyzer.auth.access import resolve_access, require_vendor_access
from vendor_risk_analyzer.db.session import (
    get_db,
)
from vendor_risk_analyzer.schemas.assessment import (
    AssessmentResponse,
)


logger = logging.getLogger(
    "uvicorn.error"
)


router = APIRouter(
    tags=["Assessments"],
)


# ============================================================
# RESPONSE BUILDERS
# ============================================================


async def load_assessment_response(
    *, db: AsyncSession, assessment_id: UUID, access
) -> AssessmentResponse:
    try:
        return await repository.load_assessment_response(
            db=db, assessment_id=assessment_id, access=access
        )
    except repository.AssessmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Assessment not found") from exc


# ============================================================
# GET ASSESSMENT
# ============================================================


@router.get(
    "/api/assessments/{assessment_id}",
    response_model=AssessmentResponse,
)
async def get_assessment(
    assessment_id: UUID,
    db: AsyncSession = Depends(
        get_db
    ),
    user: dict = Depends(
        require_roles(
            "viewer",
            "analyst",
            "admin",
        )
    ),
):
    """
    Read an existing assessment.

    This endpoint is read-only.
    """
    access = await resolve_access(db, user)

    logger.info(
        "Assessment API read started. "
        "assessment_id=%s",
        assessment_id,
    )

    response = (
        await load_assessment_response(
            db=db,
            access=access,
            assessment_id=(
                assessment_id
            ),
        )
    )

    logger.info(
        "Assessment API read completed. "
        "assessment_id=%s "
        "vendor_id=%s "
        "findings=%s",
        response.id,
        response.vendor_id,
        len(
            response.findings
        ),
    )

    return response


# ============================================================
# CREATE ASSESSMENT
# ============================================================


@router.post(
    "/api/vendors/{vendor_id}/assessments",
    response_model=AssessmentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_assessment(
    vendor_id: UUID,
    db: AsyncSession = Depends(
        get_db
    ),
    user: dict = Depends(
        require_roles(
            "analyst",
            "admin",
        )
    ),
    _: None = Depends(
        verify_csrf
    ),
    _rate_limit: None = Depends(
        rate_limit("assessment")
    ),
):
    """
    Queue a vendor risk assessment.

    The request returns immediately with status "queued". The
    background assessment worker runs retrieval, grounded LLM
    analysis, persistence, and deterministic risk policy, and
    records progress in the assessment metadata. Poll
    GET /api/assessments/{id} until status is "completed" or
    "failed".
    """
    access = await require_vendor_access(db, user, vendor_id)

    assessment = Assessment(
        vendor_id=vendor_id,
        status="queued",
        assessment_type=ASSESSMENT_TYPE,
        extra_data={
            "requested_by": user["sub"],
            "requested_by_name": (
                user.get("name")
                if isinstance(user.get("name"), str)
                else None
            ),
            "progress": {
                "stage": "queued",
                "message": "Waiting to start",
            },
        },
    )

    db.add(assessment)
    await db.commit()

    notify_new_assessment()

    logger.info(
        "Assessment queued. vendor_id=%s assessment_id=%s",
        vendor_id,
        assessment.id,
    )

    return await load_assessment_response(
        db=db,
        access=access,
        assessment_id=assessment.id,
    )
