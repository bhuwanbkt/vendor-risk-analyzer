"""Read the final persisted assessment for HTTP and MCP clients."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vendor_risk_analyzer.db.models import Assessment, Finding
from vendor_risk_analyzer.schemas.assessment import (
    AssessmentFindingResponse,
    AssessmentResponse,
)


class AssessmentNotFoundError(LookupError):
    pass


def build_finding_response(finding: Finding) -> AssessmentFindingResponse:
    return AssessmentFindingResponse(
        **{
            name: getattr(finding, name)
            for name in AssessmentFindingResponse.model_fields
            if name != "metadata"
        },
        metadata=finding.extra_data or {},
    )


async def load_assessment_response(
    *, db: AsyncSession, assessment_id: UUID, vendor_id: UUID | None = None
) -> AssessmentResponse:
    query = select(Assessment).where(Assessment.id == assessment_id)
    if vendor_id is not None:
        query = query.where(Assessment.vendor_id == vendor_id)
    assessment = (await db.execute(query)).scalar_one_or_none()
    if assessment is None:
        raise AssessmentNotFoundError("Assessment not found")
    findings = (
        await db.execute(
            select(Finding)
            .where(Finding.assessment_id == assessment.id)
            .order_by(Finding.created_at, Finding.id)
        )
    ).scalars().all()
    return AssessmentResponse(
        **{
            name: getattr(assessment, name)
            for name in AssessmentResponse.model_fields
            if name not in {"metadata", "findings"}
        },
        metadata=assessment.extra_data or {},
        findings=[build_finding_response(row) for row in findings],
    )
