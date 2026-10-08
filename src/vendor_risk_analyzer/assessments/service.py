from __future__ import annotations

import logging
from datetime import (
    datetime,
    timezone,
)
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from vendor_risk_analyzer.assessments.schemas import (
    AssessmentPersistenceResult,
    PersistedFinding,
)
from vendor_risk_analyzer.db.models import (
    Assessment,
    Document,
    DocumentChunk,
    Finding,
    Vendor,
)
from vendor_risk_analyzer.risk_analysis.schemas import (
    VendorRiskAnalysisResult,
)


logger = logging.getLogger(
    "uvicorn.error"
)


ASSESSMENT_TYPE = (
    "ai_evidence_review"
)

INITIAL_SEVERITY = "unrated"


class AssessmentPersistenceError(
    RuntimeError
):
    """Assessment persistence failed."""


class AssessmentPersistenceService:
    async def _validate_vendor(
        self,
        *,
        db: AsyncSession,
        vendor_id: UUID,
    ) -> None:
        vendor = await db.get(
            Vendor,
            vendor_id,
        )

        if vendor is None:
            raise (
                AssessmentPersistenceError(
                    "Vendor not found."
                )
            )

    async def _load_evidence_chunks(
        self,
        *,
        db: AsyncSession,
        vendor_id: UUID,
        analysis: VendorRiskAnalysisResult,
    ) -> dict[
        str,
        tuple[
            DocumentChunk,
            Document,
        ],
    ]:
        chunk_ids = {
            UUID(evidence.chunk_id)
            for evidence
            in analysis.evidence
        }

        if not chunk_ids:
            raise (
                AssessmentPersistenceError(
                    "Analysis contains no "
                    "evidence chunks."
                )
            )

        result = await db.execute(
            select(
                DocumentChunk,
                Document,
            )
            .join(
                Document,
                Document.id
                == DocumentChunk.document_id,
            )
            .where(
                DocumentChunk.id.in_(
                    chunk_ids
                )
            )
        )

        rows = result.all()

        evidence_lookup: dict[
            str,
            tuple[
                DocumentChunk,
                Document,
            ],
        ] = {}

        for (
            chunk,
            document,
        ) in rows:
            if (
                document.vendor_id
                != vendor_id
            ):
                raise (
                    AssessmentPersistenceError(
                        "Evidence chunk belongs "
                        "to another vendor."
                    )
                )

            evidence_lookup[
                str(chunk.id)
            ] = (
                chunk,
                document,
            )

        if (
            len(evidence_lookup)
            != len(chunk_ids)
        ):
            raise (
                AssessmentPersistenceError(
                    "One or more evidence "
                    "chunks no longer exist."
                )
            )

        return evidence_lookup

    def _validate_findings(
        self,
        *,
        analysis: VendorRiskAnalysisResult,
        evidence_lookup: dict[
            str,
            tuple[
                DocumentChunk,
                Document,
            ],
        ],
    ) -> None:
        for finding in analysis.findings:
            if not (
                finding.evidence_chunk_ids
            ):
                raise (
                    AssessmentPersistenceError(
                        "Finding has no "
                        "evidence chunks."
                    )
                )

            for chunk_id in (
                finding
                .evidence_chunk_ids
            ):
                if (
                    chunk_id
                    not in evidence_lookup
                ):
                    raise (
                        AssessmentPersistenceError(
                            "Finding references "
                            "unknown evidence "
                            f"chunk: {chunk_id}"
                        )
                    )

    async def persist(
        self,
        *,
        db: AsyncSession,
        analysis: VendorRiskAnalysisResult,
    ) -> AssessmentPersistenceResult:
        """
        Persist one completed AI evidence
        assessment and its grounded findings.

        This method intentionally does NOT
        calculate severity, overall risk,
        or an overall score.
        """

        try:
            vendor_id = UUID(
                analysis.vendor_id
            )

        except ValueError as exc:
            raise (
                AssessmentPersistenceError(
                    "Invalid vendor ID."
                )
            ) from exc

        logger.info(
            "Assessment persistence "
            "started. vendor_id=%s "
            "findings=%s",
            vendor_id,
            len(
                analysis.findings
            ),
        )

        await self._validate_vendor(
            db=db,
            vendor_id=vendor_id,
        )

        evidence_lookup = (
            await self
            ._load_evidence_chunks(
                db=db,
                vendor_id=vendor_id,
                analysis=analysis,
            )
        )

        self._validate_findings(
            analysis=analysis,
            evidence_lookup=(
                evidence_lookup
            ),
        )

        now = datetime.now(
            timezone.utc
        )

        assessment = Assessment(
            vendor_id=vendor_id,
            status="completed",
            assessment_type=(
                ASSESSMENT_TYPE
            ),
            overall_score=None,
            overall_risk=None,
            started_at=now,
            completed_at=now,
            extra_data={
                "analysis_model":
                    analysis.model,
                "analysis_summary":
                    analysis.summary,
                "raw_finding_count":
                    (
                        analysis
                        .raw_finding_count
                    ),
                "normalized_finding_count":
                    (
                        analysis
                        .normalized_finding_count
                    ),
                "severity_policy":
                    "unrated-v1",
                "persistence_version":
                    "1.0",
            },
        )

        db.add(
            assessment
        )

        persisted_findings: list[
            PersistedFinding
        ] = []

        try:
            # Generate the assessment UUID
            # before creating its findings.
            await db.flush()

            for analysis_finding in (
                analysis.findings
            ):
                evidence_ids = list(
                    dict.fromkeys(
                        analysis_finding
                        .evidence_chunk_ids
                    )
                )

                primary_chunk_id = (
                    evidence_ids[0]
                )

                (
                    primary_chunk,
                    primary_document,
                ) = evidence_lookup[
                    primary_chunk_id
                ]

                evidence_document_ids = (
                    list(
                        dict.fromkeys(
                            str(
                                evidence_lookup[
                                    chunk_id
                                ][1].id
                            )
                            for chunk_id
                            in evidence_ids
                        )
                    )
                )

                evidence_details = []

                for chunk_id in (
                    evidence_ids
                ):
                    (
                        chunk,
                        document,
                    ) = evidence_lookup[
                        chunk_id
                    ]

                    analysis_evidence = (
                        next(
                            (
                                item
                                for item
                                in analysis.evidence
                                if (
                                    item.chunk_id
                                    == chunk_id
                                )
                            ),
                            None,
                        )
                    )

                    evidence_details.append(
                        {
                            "chunk_id":
                                str(
                                    chunk.id
                                ),
                            "document_id":
                                str(
                                    document.id
                                ),
                            "sequence":
                                chunk.sequence,
                            "similarity":
                                (
                                    analysis_evidence
                                    .similarity
                                    if (
                                        analysis_evidence
                                        is not None
                                    )
                                    else None
                                ),
                            "retrieval_topics":
                                (
                                    analysis_evidence
                                    .retrieval_topics
                                    if (
                                        analysis_evidence
                                        is not None
                                    )
                                    else []
                                ),
                        }
                    )

                finding = Finding(
                    assessment_id=(
                        assessment.id
                    ),
                    document_id=(
                        primary_document.id
                    ),
                    document_element_id=(
                        primary_chunk
                        .source_element_id
                    ),
                    document_chunk_id=(
                        primary_chunk.id
                    ),
                    category=(
                        analysis_finding
                        .category
                    ),
                    severity=(
                        INITIAL_SEVERITY
                    ),
                    title=(
                        analysis_finding
                        .title
                    ),
                    description=(
                        analysis_finding
                        .description
                    ),
                    recommendation=None,
                    evidence_text=None,
                    confidence=(
                        analysis_finding
                        .confidence
                    ),
                    rule_id=None,
                    status="open",
                    extra_data={
                        "finding_type":
                            (
                                analysis_finding
                                .finding_type
                            ),
                        "analysis_model":
                            analysis.model,
                        "evidence_chunk_ids":
                            evidence_ids,
                        "evidence_document_ids":
                            (
                                evidence_document_ids
                            ),
                        "primary_chunk_id":
                            (
                                primary_chunk_id
                            ),
                        "evidence":
                            evidence_details,
                        "severity_policy":
                            "unrated-v1",
                    },
                )

                db.add(
                    finding
                )

                # Generate the finding UUID.
                await db.flush()

                persisted_findings.append(
                    PersistedFinding(
                        finding_id=(
                            finding.id
                        ),
                        category=(
                            finding.category
                        ),
                        finding_type=(
                            analysis_finding
                            .finding_type
                        ),
                        severity=(
                            finding.severity
                        ),
                        primary_document_id=(
                            primary_document.id
                        ),
                        primary_chunk_id=(
                            primary_chunk.id
                        ),
                    )
                )

            await db.commit()

        except Exception:
            await db.rollback()

            logger.exception(
                "Assessment persistence "
                "failed. vendor_id=%s",
                vendor_id,
            )

            raise

        logger.info(
            "Assessment persistence "
            "completed. "
            "vendor_id=%s "
            "assessment_id=%s "
            "findings=%s",
            vendor_id,
            assessment.id,
            len(
                persisted_findings
            ),
        )

        return (
            AssessmentPersistenceResult(
                assessment_id=(
                    assessment.id
                ),
                vendor_id=vendor_id,
                status=(
                    assessment.status
                ),
                assessment_type=(
                    assessment
                    .assessment_type
                ),
                finding_count=len(
                    persisted_findings
                ),
                findings=(
                    persisted_findings
                ),
            )
        )