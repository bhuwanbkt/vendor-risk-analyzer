from fastapi import APIRouter, Response
from starlette import status

from vendor_risk_analyzer.db.session import check_database


router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "vendor-risk-analyzer",
        "version": "0.1.0",
    }


@router.get("/ready")
async def readiness_check(
    response: Response,
) -> dict[str, str]:

    try:
        await check_database()

    except Exception:
        response.status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
        )

        return {
            "status": "not_ready",
            "database": "unavailable",
        }

    return {
        "status": "ready",
        "database": "connected",
    }