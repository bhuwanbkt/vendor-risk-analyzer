from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class AssessmentFindingResponse(BaseModel):
    id: UUID
    assessment_id: UUID

    document_id: UUID | None
    document_element_id: UUID | None
    document_chunk_id: UUID | None

    category: str
    severity: str

    title: str
    description: str

    recommendation: str | None
    confidence: float | None

    rule_id: str | None
    status: str

    metadata: dict[str, Any]

    created_at: datetime
    updated_at: datetime


class AssessmentResponse(BaseModel):
    id: UUID
    vendor_id: UUID

    status: str
    assessment_type: str

    overall_score: float | None
    overall_risk: str | None

    started_at: datetime | None
    completed_at: datetime | None

    metadata: dict[str, Any]

    created_at: datetime
    updated_at: datetime

    findings: list[
        AssessmentFindingResponse
    ]