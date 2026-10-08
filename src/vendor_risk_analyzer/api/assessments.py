from __future__ import annotations

import logging
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from vendor_risk_analyzer.auth.dependencies import (
    require_roles,
)
from vendor_risk_analyzer.db.models import (
    Assessment,
    Finding,
)
from vendor_risk_analyzer.db.session import (
    get_db,
)
from vendor_risk_analyzer.schemas.assessment import (
    AssessmentFindingResponse,
    AssessmentResponse,
)


logger = logging.getLogger(
    "uvicorn.error"
)


router = APIRouter(
    prefix="/api/assessments",
    tags=["Assessments"],
)


def build_finding_response(
    finding: Finding,
) -> AssessmentFindingResponse:
    return AssessmentFindingResponse(
        id=finding.id,
        assessment_id=(
            finding.assessment_id
        ),
        document_id=(
            finding.document_id
        ),
        document_element_id=(
            finding.document_element_id
        ),
        document_chunk_id=(
            finding.document_chunk_id
        ),
        category=finding.category,
        severity=finding.severity,
        title=finding.title,
        description=(
            finding.description
        ),
        recommendation=(
            finding.recommendation
        ),
        confidence=(
            finding.confidence
        ),
        rule_id=(
            finding.rule_id
        ),
        status=finding.status,
        metadata=(
            finding.extra_data
            or {}
        ),
        created_at=(
            finding.created_at
        ),
        updated_at=(
            finding.updated_at
        ),
    )


@router.get(
    "/{assessment_id}",
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
    logger.info(
        "Assessment API read started. "
        "assessment_id=%s",
        assessment_id,
    )

    assessment = await db.get(
        Assessment,
        assessment_id,
    )

    if assessment is None:
        logger.warning(
            "Assessment API read failed. "
            "assessment_id=%s "
            "reason=not_found",
            assessment_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "Assessment not found"
            ),
        )

    result = await db.execute(
        select(
            Finding
        )
        .where(
            Finding.assessment_id
            == assessment.id
        )
        .order_by(
            Finding.created_at
        )
    )

    findings = list(
        result.scalars().all()
    )

    logger.info(
        "Assessment API read completed. "
        "assessment_id=%s "
        "vendor_id=%s "
        "findings=%s",
        assessment.id,
        assessment.vendor_id,
        len(findings),
    )

    return AssessmentResponse(
        id=assessment.id,
        vendor_id=(
            assessment.vendor_id
        ),
        status=(
            assessment.status
        ),
        assessment_type=(
            assessment.assessment_type
        ),
        overall_score=(
            assessment.overall_score
        ),
        overall_risk=(
            assessment.overall_risk
        ),
        started_at=(
            assessment.started_at
        ),
        completed_at=(
            assessment.completed_at
        ),
        metadata=(
            assessment.extra_data
            or {}
        ),
        created_at=(
            assessment.created_at
        ),
        updated_at=(
            assessment.updated_at
        ),
        findings=[
            build_finding_response(
                finding
            )
            for finding
            in findings
        ],
    )