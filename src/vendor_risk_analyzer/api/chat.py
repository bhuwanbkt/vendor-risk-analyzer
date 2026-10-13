from __future__ import annotations

import logging
from uuid import UUID

from vendor_risk_analyzer.auth.access import resolve_access
from vendor_risk_analyzer.chat.history import (
    conversation_detail,
    delete_conversation,
    get_owned_conversation,
    list_conversations,
    load_history,
    save_exchange,
)
from vendor_risk_analyzer.chat.schemas import ConversationDetail, ConversationSummary
from vendor_risk_analyzer.audit.service import record as audit
from vendor_risk_analyzer.usage.service import record_chat_usage
from vendor_risk_analyzer.rate_limit import rate_limit
from vendor_risk_analyzer.auth.access import require_vendor_access

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
    _rate_limit: None = Depends(
        rate_limit("chat")
    ),
):
    """
    Grounded vendor-scoped chat.

    vendor_id must always come explicitly
    from the application context.
    """
    access = await require_vendor_access(db, user, payload.vendor_id)

    # Content is never logged; only that a question was asked.
    await audit(
        access,
        user,
        "chat.question_asked",
        target_type="vendor",
        target_id=payload.vendor_id,
        vendor_id=payload.vendor_id,
        details={
            "question_length": len(payload.question),
            "via": "web",
        },
    )

    logger.info(
        "Vendor chat API started. "
        "vendor_id=%s",
        payload.vendor_id,
    )


    conversation = None
    history = payload.history

    if payload.conversation_id is not None:
        conversation = await get_owned_conversation(
            db,
            access,
            payload.conversation_id,
            vendor_id=payload.vendor_id,
        )
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found.",
            )
        history = await load_history(db, conversation)

    embedding_service = (
        EmbeddingService()
    )
    chat_service = None


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


        response = await chat_service.answer(
            db=db,
            vendor_id=(
                payload.vendor_id
            ),
            question=(
                payload.question
            ),
            history=history,
        )

        # Saving is secondary: never lose an answer over it.
        try:
            conversation_id = await save_exchange(
                db,
                access,
                conversation=conversation,
                vendor_id=payload.vendor_id,
                question=payload.question,
                response=response,
            )
        except Exception:
            logger.exception(
                "Chat history could not be saved. vendor_id=%s",
                payload.vendor_id,
            )
            await db.rollback()
            return response

        return response.model_copy(
            update={"conversation_id": conversation_id}
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
        await record_chat_usage(
            chat_service,
            access,
            vendor_id=payload.vendor_id,
            reference_id=payload.conversation_id,
            feature="chat",
        )


# ============================================================
# SAVED CONVERSATIONS
# ============================================================

ChatUser = Depends(require_roles("analyst", "admin"))


@router.get(
    "/chat/conversations",
    response_model=list[ConversationSummary],
)
async def list_chat_conversations(
    vendor_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = ChatUser,
):
    """The caller's own conversations, newest first."""
    access = (
        await require_vendor_access(db, user, vendor_id)
        if vendor_id is not None
        else await resolve_access(db, user)
    )
    return await list_conversations(db, access, vendor_id=vendor_id)


@router.get(
    "/chat/conversations/{conversation_id}",
    response_model=ConversationDetail,
)
async def get_chat_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = ChatUser,
):
    access = await resolve_access(db, user)
    conversation = await get_owned_conversation(db, access, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return await conversation_detail(db, conversation)


@router.delete(
    "/chat/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_chat_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = ChatUser,
    _: None = Depends(verify_csrf),
):
    access = await resolve_access(db, user)
    conversation = await get_owned_conversation(db, access, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    vendor_id = conversation.vendor_id
    await delete_conversation(db, conversation)
    await audit(
        access,
        user,
        "chat.conversation_deleted",
        target_type="conversation",
        target_id=conversation_id,
        vendor_id=vendor_id,
    )

