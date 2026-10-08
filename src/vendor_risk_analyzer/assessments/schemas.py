from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class PersistedFinding(BaseModel):
    finding_id: UUID
    category: str
    finding_type: str
    severity: str
    primary_document_id: UUID
    primary_chunk_id: UUID


class AssessmentPersistenceResult(
    BaseModel
):
    assessment_id: UUID
    vendor_id: UUID
    status: str
    assessment_type: str
    finding_count: int
    findings: list[PersistedFinding]