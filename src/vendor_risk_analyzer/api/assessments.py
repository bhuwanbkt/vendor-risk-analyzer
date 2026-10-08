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

from vendor_risk_analyzer.assessments.service import (
    AssessmentPersistenceError,
    AssessmentPersistenceService,
)
from vendor_risk_analyzer.auth.dependencies import (
    require_roles,
    verify_csrf,
)
from vendor_risk_analyzer.db.models import (
    Assessment,
    Finding,
)
from vendor_risk_analyzer.db.session import (
    get_db,
)
from vendor_risk_analyzer.embeddings.service import (
    EmbeddingService,
)
from vendor_risk_analyzer.retrieval.service import (
    SemanticRetriever,
    VendorNotFoundError,
)
from vendor_risk_analyzer.risk_analysis.service import (
    RiskAnalysisError,
    RiskAnalysisGroundingError,
    RiskAnalysisService,
)
from vendor_risk_analyzer.risk_policy.service import (
    RiskPolicyError,
    RiskPolicyService,
)
from vendor_risk_analyzer.schemas.assessment import (
    AssessmentFindingResponse,
    AssessmentResponse,
)


logger = logging.getLogger(
    "uvicorn.error"
)


router = APIRouter(
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
        category=(
            finding.category
        ),
        severity=(
            finding.severity
        ),
        title=(
            finding.title
        ),
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
        status=(
            finding.status
        ),
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


async def load_assessment_response(
    *,
    db: AsyncSession,
    assessment_id: UUID,
) -> AssessmentResponse:
    assessment = await db.get(
        Assessment,
        assessment_id,
    )

    if assessment is None:
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

    return AssessmentResponse(
        id=(
            assessment.id
        ),
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
    logger.info(
        "Assessment API read started. "
        "assessment_id=%s",
        assessment_id,
    )

    response = (
        await load_assessment_response(
            db=db,
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


@router.post(
    "/api/vendors/{vendor_id}/assessments",
    response_model=AssessmentResponse,
    status_code=status.HTTP_201_CREATED,
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
):
    """
    Run a complete vendor risk assessment.

    Flow:
        semantic retrieval
        -> grounded LLM analysis
        -> assessment persistence
        -> deterministic policy
        -> completed API response
    """

    logger.info(
        "Assessment API creation started. "
        "vendor_id=%s",
        vendor_id,
    )

    embedding_service = None
    risk_service = None

    try:
        # ----------------------------------------------------
        # STEP 1
        # Create retrieval dependencies.
        # ----------------------------------------------------

        embedding_service = (
            EmbeddingService()
        )

        retriever = (
            SemanticRetriever(
                embedding_service
            )
        )

        # ----------------------------------------------------
        # STEP 2
        # Run grounded vendor risk analysis.
        #
        # RiskAnalysisService itself does not
        # persist database rows.
        # ----------------------------------------------------

        risk_service = (
            RiskAnalysisService(
                retriever=retriever
            )
        )

        analysis = (
            await risk_service
            .analyze_vendor(
                db=db,
                vendor_id=(
                    vendor_id
                ),
            )
        )

        logger.info(
            "Assessment API analysis "
            "completed. "
            "vendor_id=%s "
            "raw_findings=%s "
            "normalized_findings=%s",
            vendor_id,
            (
                analysis
                .raw_finding_count
            ),
            (
                analysis
                .normalized_finding_count
            ),
        )

        # ----------------------------------------------------
        # STEP 3
        # Persist the grounded assessment.
        #
        # Findings initially persist with:
        #
        # severity = unrated
        # overall_risk = NULL
        # ----------------------------------------------------

        persistence_service = (
            AssessmentPersistenceService()
        )

        persisted = (
            await persistence_service
            .persist(
                db=db,
                analysis=analysis,
            )
        )

        logger.info(
            "Assessment API persistence "
            "completed. "
            "vendor_id=%s "
            "assessment_id=%s "
            "findings=%s",
            vendor_id,
            persisted.assessment_id,
            persisted.finding_count,
        )

        # ----------------------------------------------------
        # STEP 4
        # Apply deterministic policy.
        #
        # This updates:
        #   findings.severity
        #   findings.rule_id
        #   assessment.overall_risk
        #
        # It still does NOT create an
        # overall numeric score.
        # ----------------------------------------------------

        policy_service = (
            RiskPolicyService()
        )

        policy_result = (
            await policy_service
            .apply_assessment(
                db=db,
                assessment_id=(
                    persisted
                    .assessment_id
                ),
            )
        )

        logger.info(
            "Assessment API policy "
            "completed. "
            "assessment_id=%s "
            "overall_risk=%s",
            persisted.assessment_id,
            (
                policy_result
                .proposed_overall_risk
            ),
        )

        # ----------------------------------------------------
        # STEP 5
        # Load the final database state.
        #
        # We deliberately return what was
        # actually persisted instead of
        # reconstructing the response from
        # intermediate Python objects.
        # ----------------------------------------------------

        response = (
            await load_assessment_response(
                db=db,
                assessment_id=(
                    persisted
                    .assessment_id
                ),
            )
        )

        logger.info(
            "Assessment API creation "
            "completed. "
            "vendor_id=%s "
            "assessment_id=%s "
            "overall_risk=%s "
            "findings=%s",
            vendor_id,
            response.id,
            response.overall_risk,
            len(
                response.findings
            ),
        )

        return response

    except VendorNotFoundError as exc:
        logger.warning(
            "Assessment API creation "
            "failed. "
            "vendor_id=%s "
            "reason=vendor_not_found",
            vendor_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "Vendor not found"
            ),
        ) from exc

    except RiskAnalysisGroundingError as exc:
        logger.exception(
            "Assessment API grounding "
            "validation failed. "
            "vendor_id=%s",
            vendor_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_502_BAD_GATEWAY
            ),
            detail=(
                "Risk analysis grounding "
                "validation failed"
            ),
        ) from exc

    except RiskAnalysisError as exc:
        logger.exception(
            "Assessment API risk analysis "
            "failed. "
            "vendor_id=%s",
            vendor_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_502_BAD_GATEWAY
            ),
            detail=(
                "Risk analysis failed"
            ),
        ) from exc

    except AssessmentPersistenceError as exc:
        logger.exception(
            "Assessment API persistence "
            "failed. "
            "vendor_id=%s",
            vendor_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Assessment persistence "
                "failed"
            ),
        ) from exc

    except RiskPolicyError as exc:
        logger.exception(
            "Assessment API policy "
            "application failed. "
            "vendor_id=%s",
            vendor_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Risk policy application "
                "failed"
            ),
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Assessment API creation "
            "failed unexpectedly. "
            "vendor_id=%s",
            vendor_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Assessment creation failed"
            ),
        ) from exc

    finally:
        # RiskAnalysisService owns the
        # Gemini analysis client.
        if risk_service is not None:
            risk_service.close()

        # EmbeddingService owns the
        # Gemini embedding client.
        if embedding_service is not None:
            await embedding_service.close()