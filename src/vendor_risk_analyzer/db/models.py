from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vendor_risk_analyzer.db.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # NULL is reserved for the locked legacy workspace awaiting operator binding.
    zitadel_org_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    default_access: Mapped[str] = mapped_column(String(16), default="assigned", nullable=False)
    __table_args__ = (CheckConstraint("default_access IN ('all', 'assigned')", name="ck_tenant_default_access"),)


class TenantMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenant_memberships"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    access_mode: Mapped[str] = mapped_column(String(16), default="assigned", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "subject", name="uq_membership_tenant_subject"),
        UniqueConstraint("tenant_id", "id", name="uq_membership_tenant_id"),
        CheckConstraint("access_mode IN ('all', 'assigned')", name="ck_membership_access_mode"),
    )


class Vendor(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "vendors"

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    website: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        default="active",
        nullable=False,
    )

    extra_data: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    documents: Mapped[list[Document]] = relationship(
        back_populates="vendor",
        cascade="all, delete-orphan",
    )

    assessments: Mapped[list[Assessment]] = relationship(
        back_populates="vendor",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_vendor_tenant_name"),
        UniqueConstraint("tenant_id", "id", name="uq_vendor_tenant_id"),
    )


class VendorAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vendor_assignments"
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    member_id: Mapped[UUID] = mapped_column(nullable=False)
    vendor_id: Mapped[UUID] = mapped_column(nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "member_id"], ["tenant_memberships.tenant_id", "tenant_memberships.id"], ondelete="CASCADE", name="fk_assignment_membership"),
        ForeignKeyConstraint(["tenant_id", "vendor_id"], ["vendors.tenant_id", "vendors.id"], ondelete="CASCADE", name="fk_assignment_vendor"),
        UniqueConstraint("member_id", "vendor_id", name="uq_assignment_member_vendor"),
        Index("ix_assignment_tenant_member", "tenant_id", "member_id"),
    )


class Document(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "documents"

    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "vendors.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    filename: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    object_key: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
        unique=True,
    )

    file_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    mime_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    size_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        default="uploaded",
        nullable=False,
        index=True,
    )

    page_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    parser_version: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    extra_data: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    vendor: Mapped[Vendor] = relationship(
        back_populates="documents",
    )

    elements: Mapped[list[DocumentElement]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )

    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "vendor_id",
            "sha256",
            name="uq_document_vendor_sha256",
        ),
    )


class DocumentElement(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "document_elements"

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    element_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    page_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    sheet_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    section_title: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    heading_path: Mapped[list] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )

    bounding_box: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    extra_data: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    document: Mapped[Document] = relationship(
        back_populates="elements",
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "sequence",
            name="uq_document_element_sequence",
        ),
    )


class DocumentChunk(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "document_chunks"

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    source_element_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "document_elements.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    token_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(768),
        nullable=True,
    )

    extra_data: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    document: Mapped[Document] = relationship(
        back_populates="chunks",
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "sequence",
            name="uq_document_chunk_sequence",
        ),
    )


class Assessment(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "assessments"

    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "vendors.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        default="queued",
        nullable=False,
        index=True,
    )

    assessment_type: Mapped[str] = mapped_column(
        String(100),
        default="vendor_risk",
        nullable=False,
    )

    overall_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    overall_risk: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    extra_data: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    vendor: Mapped[Vendor] = relationship(
        back_populates="assessments",
    )

    findings: Mapped[list[Finding]] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
    )


class Finding(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "findings"

    assessment_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "assessments.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    document_element_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "document_elements.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    document_chunk_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "document_chunks.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    severity: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    recommendation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    evidence_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    rule_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        default="open",
        nullable=False,
    )

    extra_data: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    assessment: Mapped[Assessment] = relationship(
        back_populates="findings",
    )

    __table_args__ = (
        Index(
            "ix_findings_assessment_severity",
            "assessment_id",
            "severity",
        ),
    )


class AuditEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Who did what, per company. Rows are append-only.

    vendor_id is not a foreign key so events outlive deleted vendors.
    """

    __tablename__ = "audit_events"

    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True
    )
    actor_subject: Mapped[str | None] = mapped_column(String(255))
    actor_name: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(64))
    vendor_id: Mapped[UUID | None] = mapped_column(nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        Index("ix_audit_events_tenant_created", "tenant_id", "created_at"),
    )


class ChatConversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's saved AI chat about one vendor. Private to its owner."""

    __tablename__ = "chat_conversations"

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    member_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    __table_args__ = (
        Index(
            "ix_chat_conversations_owner",
            "tenant_id",
            "member_subject",
            "vendor_id",
            "updated_at",
        ),
    )


class ChatMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_messages"

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    model: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_message_role"),
    )


class LlmUsageEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Gemini tokens spent by one assessment or chat answer, per company.

    vendor_id is not a foreign key so usage history outlives deleted vendors.
    """

    __tablename__ = "llm_usage_events"

    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True
    )
    vendor_id: Mapped[UUID | None] = mapped_column(nullable=True)
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    reference_id: Mapped[str | None] = mapped_column(String(64))
    actor_subject: Mapped[str | None] = mapped_column(String(255))
    requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    thought_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("ix_llm_usage_tenant_created", "tenant_id", "created_at"),
    )


class RateLimitBucket(Base):
    """Shared rate-limit state: one row per user and limit.

    ``tat`` is the GCRA "theoretical arrival time" in Unix seconds. A row
    whose ``tat`` is in the past is equivalent to no row and may be deleted.
    """

    __tablename__ = "rate_limit_buckets"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    tat: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
