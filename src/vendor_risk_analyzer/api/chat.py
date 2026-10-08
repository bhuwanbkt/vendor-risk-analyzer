from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from vendor_risk_analyzer.auth.dependencies import (
    require_roles,
    verify_csrf,
)

from vendor_risk_analyzer.chat.schemas import (
    ChatRequest,
    ChatResponse,
)

from vendor_risk_analyzer.chat.service import (
    ChatGroundingError,
    ChatService,
    ChatServiceError,
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


logger = logging.getLogger(
    "uvicorn.error"
)


router = APIRouter(
    prefix="/api",
    tags=["Chat"],
)


@router.post(
    "/chat",
    response_model=ChatResponse,
)
async def vendor_chat(
    payload: ChatRequest,
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
    Grounded vendor-scoped chat.

    vendor_id must always come explicitly
    from the application context.
    """

    logger.info(
        "Vendor chat API started. "
        "vendor_id=%s",
        payload.vendor_id,
    )


    embedding_service = (
        EmbeddingService()
    )


    try:
        retriever = (
            SemanticRetriever(
                embedding_service
            )
        )


        chat_service = (
            ChatService(
                retriever=retriever
            )
        )


        return await chat_service.answer(
            db=db,
            vendor_id=(
                payload.vendor_id
            ),
            question=(
                payload.question
            ),
            history=(
                payload.history
            ),
        )


    except VendorNotFoundError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail="Vendor not found.",
        ) from exc


    except ChatGroundingError as exc:
        logger.exception(
            "Vendor chat grounding "
            "validation failed. "
            "vendor_id=%s",
            payload.vendor_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_502_BAD_GATEWAY
            ),
            detail=(
                "The AI response could not "
                "be grounded safely."
            ),
        ) from exc


    except ChatServiceError as exc:
        logger.exception(
            "Vendor chat failed. "
            "vendor_id=%s",
            payload.vendor_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_502_BAD_GATEWAY
            ),
            detail=str(exc),
        ) from exc


    finally:
        await embedding_service.close()