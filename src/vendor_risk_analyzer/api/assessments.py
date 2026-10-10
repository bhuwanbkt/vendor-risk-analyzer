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

from vendor_risk_analyzer.assessments import repository
from vendor_risk_analyzer.assessments.service import (
    AssessmentPersistenceError,
    AssessmentPersistenceService,
)
from vendor_risk_analyzer.auth.dependencies import (
    require_roles,
    verify_csrf,
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
    *, db: AsyncSession, assessment_id: UUID
) -> AssessmentResponse:
    try:
        return await repository.load_assessment_response(
            db=db, assessment_id=assessment_id
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


# ============================================================
# CREATE ASSESSMENT
# ============================================================


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

        vendor_id
            ↓
        semantic retrieval
            ↓
        grounded LLM analysis
            ↓
        assessment persistence
            ↓
        deterministic risk policy
            ↓
        persisted AssessmentResponse

    Important:

    - vendor scope is explicit
    - no cross-vendor fallback
    - LLM findings must cite retrieved evidence
    - LLM does not assign final severity
    - deterministic policy assigns severity
    - no numeric overall score is invented
    """

    logger.info(
        "Assessment API creation started. "
        "vendor_id=%s",
        vendor_id,
    )

    # --------------------------------------------------------
    # FastAPI converts the path parameter to UUID.
    #
    # RiskAnalysisService currently returns a
    # VendorRiskAnalysisResult whose vendor_id
    # field expects a string.
    #
    # Normalize once at the API boundary.
    # --------------------------------------------------------

    normalized_vendor_id = str(
        vendor_id
    )

    embedding_service = None
    risk_service = None

    try:
        # ====================================================
        # STEP 1
        # Create embedding + retrieval dependencies
        # ====================================================

        embedding_service = (
            EmbeddingService()
        )

        retriever = (
            SemanticRetriever(
                embedding_service
            )
        )


        # ====================================================
        # STEP 2
        # Create the grounded risk analysis service
        # ====================================================

        risk_service = (
            RiskAnalysisService(
                retriever=retriever
            )
        )


        # ====================================================
        # STEP 3
        # Run vendor-scoped retrieval + LLM analysis
        #
        # IMPORTANT FIX:
        #
        # Pass normalized_vendor_id as STRING,
        # not FastAPI's UUID object.
        # ====================================================

        analysis = (
            await risk_service
            .analyze_vendor(
                db=db,
                vendor_id=(
                    normalized_vendor_id
                ),
            )
        )


        logger.info(
            "Assessment API analysis "
            "completed. "
            "vendor_id=%s "
            "raw_findings=%s "
            "normalized_findings=%s",
            normalized_vendor_id,
            (
                analysis
                .raw_finding_count
            ),
            (
                analysis
                .normalized_finding_count
            ),
        )


        # ====================================================
        # STEP 4
        # Persist grounded findings
        #
        # Persistence initially stores:
        #
        # severity = unrated
        # overall_risk = NULL
        # overall_score = NULL
        #
        # The deterministic policy runs next.
        # ====================================================

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
            "assessment_id=%s",
            normalized_vendor_id,
            persisted.assessment_id,
        )


        # ====================================================
        # STEP 5
        # Apply deterministic risk policy
        #
        # This may update:
        #
        # findings.severity
        # findings.rule_id
        # findings.metadata
        # assessment.overall_risk
        # assessment.metadata
        #
        # It does NOT invent an overall_score.
        # ====================================================

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


        # ====================================================
        # STEP 6
        # Read final persisted state
        #
        # We return the actual PostgreSQL state
        # after persistence + policy application.
        # ====================================================

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
            normalized_vendor_id,
            response.id,
            response.overall_risk,
            len(
                response.findings
            ),
        )


        return response


    # ========================================================
    # VENDOR NOT FOUND
    # ========================================================

    except VendorNotFoundError as exc:
        logger.warning(
            "Assessment API creation "
            "failed. "
            "vendor_id=%s "
            "reason=vendor_not_found",
            normalized_vendor_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "Vendor not found"
            ),
        ) from exc


    # ========================================================
    # GROUNDING VALIDATION FAILURE
    # ========================================================

    except RiskAnalysisGroundingError as exc:
        logger.exception(
            "Assessment API grounding "
            "validation failed. "
            "vendor_id=%s",
            normalized_vendor_id,
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


    # ========================================================
    # RISK ANALYSIS FAILURE
    # ========================================================

    except RiskAnalysisError as exc:
        logger.exception(
            "Assessment API risk analysis "
            "failed. "
            "vendor_id=%s",
            normalized_vendor_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_502_BAD_GATEWAY
            ),
            detail=(
                "Risk analysis failed"
            ),
        ) from exc


    # ========================================================
    # PERSISTENCE FAILURE
    # ========================================================

    except AssessmentPersistenceError as exc:
        logger.exception(
            "Assessment API persistence "
            "failed. "
            "vendor_id=%s",
            normalized_vendor_id,
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


    # ========================================================
    # POLICY FAILURE
    # ========================================================

    except RiskPolicyError as exc:
        logger.exception(
            "Assessment API policy "
            "application failed. "
            "vendor_id=%s",
            normalized_vendor_id,
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


    # ========================================================
    # EXISTING FASTAPI HTTP ERRORS
    # ========================================================

    except HTTPException:
        raise


    # ========================================================
    # UNEXPECTED FAILURE
    # ========================================================

    except Exception as exc:
        logger.exception(
            "Assessment API creation "
            "failed unexpectedly. "
            "vendor_id=%s",
            normalized_vendor_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Assessment creation failed"
            ),
        ) from exc


    # ========================================================
    # CLEANUP
    # ========================================================

    finally:
        # Close the risk-analysis client.
        if (
            risk_service
            is not None
        ):
            risk_service.close()


        # Close the embedding client.
        if (
            embedding_service
            is not None
        ):
            await embedding_service.close()