from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
)


class ChatHistoryMessage(
    BaseModel
):
    role: Literal[
        "user",
        "assistant",
    ]

    content: str = Field(
        min_length=1,
        max_length=1500,
    )


class ChatRequest(
    BaseModel
):
    vendor_id: UUID

    question: str = Field(
        min_length=1,
        max_length=2000,
    )

    history: list[
        ChatHistoryMessage
    ] = Field(
        default_factory=list,
        max_length=6,
    )
    # Continue a saved conversation. Its history then comes from the
    # server, and any client-supplied history is ignored.
    conversation_id: UUID | None = None


class ChatSource(
    BaseModel
):
    source_number: int

    chunk_id: str

    document_id: str

    sequence: int

    similarity: float


class ChatResponse(
    BaseModel
):
    vendor_id: UUID

    answer: str

    model: str

    sources: list[
        ChatSource
    ]
    conversation_id: UUID | None = None


class ConversationSummary(BaseModel):
    id: UUID
    vendor_id: UUID
    title: str
    updated_at: str


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    sources: list[ChatSource]
    created_at: str


class ConversationDetail(ConversationSummary):
    messages: list[ConversationMessage]

