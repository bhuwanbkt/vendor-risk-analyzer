"""Saved chat conversations.

Conversations are private to the company member who created them and are
only visible while that member can still access the vendor. When a
conversation continues, its history is read from the database, never from
the browser.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, select

from vendor_risk_analyzer.chat.schemas import (
    ChatHistoryMessage,
    ChatResponse,
    ConversationDetail,
    ConversationMessage,
    ConversationSummary,
)
from vendor_risk_analyzer.db.models import ChatConversation, ChatMessage

HISTORY_MESSAGES = 6
TITLE_LENGTH = 80


def _owned(access):
    return (
        ChatConversation.tenant_id == access.tenant.id,
        ChatConversation.member_subject == access.member.subject,
        ChatConversation.vendor_id.in_(access.vendor_ids()),
    )


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def make_title(question: str) -> str:
    text = " ".join(question.split())
    return text if len(text) <= TITLE_LENGTH else text[: TITLE_LENGTH - 1].rstrip() + "…"


async def get_owned_conversation(db, access, conversation_id: UUID, *, vendor_id: UUID | None = None):
    query = select(ChatConversation).where(
        ChatConversation.id == conversation_id, *_owned(access)
    )
    if vendor_id is not None:
        query = query.where(ChatConversation.vendor_id == vendor_id)
    return await db.scalar(query)


async def load_history(db, conversation) -> list[ChatHistoryMessage]:
    rows = (
        await db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(HISTORY_MESSAGES)
        )
    ).scalars().all()
    return [
        ChatHistoryMessage(role=row.role, content=row.content[:1500])
        for row in reversed(rows)
    ]


async def save_exchange(
    db,
    access,
    *,
    conversation,
    vendor_id: UUID,
    question: str,
    response: ChatResponse,
) -> UUID:
    now = datetime.now(timezone.utc)
    if conversation is None:
        conversation = ChatConversation(
            tenant_id=access.tenant.id,
            vendor_id=vendor_id,
            member_subject=access.member.subject,
            title=make_title(question),
        )
        db.add(conversation)
        await db.flush()
    conversation.updated_at = now
    db.add(ChatMessage(conversation_id=conversation.id, role="user", content=question, created_at=now))
    db.add(
        ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=response.answer,
            sources=[source.model_dump() for source in response.sources],
            model=response.model,
            # Strictly after the question so ordering is stable.
            created_at=now + timedelta(microseconds=1),
        )
    )
    await db.commit()
    return conversation.id


async def list_conversations(db, access, *, vendor_id: UUID | None = None, limit: int = 50):
    query = (
        select(ChatConversation)
        .where(*_owned(access))
        .order_by(ChatConversation.updated_at.desc(), ChatConversation.id.desc())
        .limit(limit)
    )
    if vendor_id is not None:
        query = query.where(ChatConversation.vendor_id == vendor_id)
    rows = (await db.execute(query)).scalars().all()
    return [
        ConversationSummary(
            id=row.id, vendor_id=row.vendor_id, title=row.title, updated_at=_iso(row.updated_at)
        )
        for row in rows
    ]


async def conversation_detail(db, conversation) -> ConversationDetail:
    rows = (
        await db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        )
    ).scalars().all()
    return ConversationDetail(
        id=conversation.id,
        vendor_id=conversation.vendor_id,
        title=conversation.title,
        updated_at=_iso(conversation.updated_at),
        messages=[
            ConversationMessage(
                role=row.role,
                content=row.content,
                sources=row.sources or [],
                created_at=_iso(row.created_at),
            )
            for row in rows
        ],
    )


async def delete_conversation(db, conversation) -> None:
    # Explicit deletes (not ORM cascade) keep this working on any session.
    await db.execute(delete(ChatMessage).where(ChatMessage.conversation_id == conversation.id))
    await db.execute(delete(ChatConversation).where(ChatConversation.id == conversation.id))
    await db.commit()
