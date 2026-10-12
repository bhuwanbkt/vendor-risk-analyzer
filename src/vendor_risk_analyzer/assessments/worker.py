"""Background assessment worker.

The HTTP API only records a "queued" assessment. This worker, running inside
the application process, claims queued assessments one at a time, runs
retrieval + LLM analysis + persistence + risk policy, and records progress so
the browser can show it.

Lifecycle: queued -> running -> completed | failed

- Claims use FOR UPDATE SKIP LOCKED, so several app replicas never run the
  same assessment.
- An assessment interrupted by shutdown is returned to "queued" and resumes
  after restart. One left "running" without progress for
  ASSESSMENT_STALE_MINUTES (a crash) is marked failed rather than retried
  automatically, so a failing assessment cannot loop on Gemini spend.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from vendor_risk_analyzer.assessments.service import (
    AssessmentPersistenceError,
    AssessmentPersistenceService,
)
from vendor_risk_analyzer.db.models import Assessment
from vendor_risk_analyzer.db.url import get_database_url
from vendor_risk_analyzer.embeddings.service import EmbeddingError, EmbeddingService
from vendor_risk_analyzer.retrieval.service import SemanticRetriever
from vendor_risk_analyzer.risk_analysis.service import (
    RiskAnalysisError,
    RiskAnalysisGroundingError,
    RiskAnalysisService,
)
from vendor_risk_analyzer.risk_policy.service import RiskPolicyError, RiskPolicyService

logger = logging.getLogger("uvicorn.error")


# Set by the API after queuing, so the worker starts without waiting for
# the next poll.
_wake = asyncio.Event()


def notify_new_assessment() -> None:
    _wake.set()


def _int_env(name: str, default: int, minimum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return value


def user_message(exc: BaseException) -> str:
    """Error text shown on the report; never includes provider internals."""
    if isinstance(exc, RiskAnalysisGroundingError):
        return "The AI response could not be grounded in the vendor's evidence."
    if isinstance(exc, RiskAnalysisError):
        return f"Risk analysis failed: {exc}"
    if isinstance(exc, EmbeddingError):
        return "The embedding service is unavailable. Try again later."
    if isinstance(exc, AssessmentPersistenceError):
        return "Saving the assessment failed."
    if isinstance(exc, RiskPolicyError):
        return "Applying the risk policy failed."
    return "The assessment failed unexpectedly."


async def claim_next_assessment(session_factory) -> UUID | None:
    async with session_factory() as db:
        result = await db.execute(
            text(
                """
                WITH candidate AS (
                    SELECT id
                    FROM assessments
                    WHERE status = 'queued'
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE assessments AS a
                SET status = 'running',
                    started_at = NOW(),
                    updated_at = NOW(),
                    metadata = COALESCE(a.metadata, '{}'::jsonb)
                        || jsonb_build_object(
                            'progress',
                            jsonb_build_object(
                                'stage', 'starting',
                                'message', 'Starting assessment'
                            )
                        )
                FROM candidate
                WHERE a.id = candidate.id
                RETURNING a.id
                """
            )
        )
        row = result.first()
        await db.commit()
    return row[0] if row else None


async def set_progress(session_factory, assessment_id: UUID, stage: str, message: str) -> None:
    async with session_factory() as db:
        await db.execute(
            text(
                """
                UPDATE assessments
                SET metadata = COALESCE(metadata, '{}'::jsonb)
                        || jsonb_build_object(
                            'progress',
                            jsonb_build_object(
                                'stage', CAST(:stage AS text),
                                'message', CAST(:message AS text)
                            )
                        ),
                    updated_at = NOW()
                WHERE id = :id AND status = 'running'
                """
            ),
            {"id": assessment_id, "stage": stage, "message": message[:200]},
        )
        await db.commit()


async def mark_failed(session_factory, assessment_id: UUID, message: str) -> None:
    async with session_factory() as db:
        await db.execute(
            text(
                """
                UPDATE assessments
                SET status = 'failed',
                    completed_at = NOW(),
                    updated_at = NOW(),
                    metadata = COALESCE(metadata, '{}'::jsonb)
                        || jsonb_build_object(
                            'error', CAST(:message AS text),
                            'progress',
                            jsonb_build_object(
                                'stage', 'failed',
                                'message', CAST(:message AS text)
                            )
                        )
                WHERE id = :id AND status IN ('queued', 'running')
                """
            ),
            {"id": assessment_id, "message": message[:500]},
        )
        await db.commit()


async def requeue(session_factory, assessment_id: UUID) -> None:
    async with session_factory() as db:
        await db.execute(
            text(
                """
                UPDATE assessments
                SET status = 'queued',
                    started_at = NULL,
                    updated_at = NOW(),
                    metadata = COALESCE(metadata, '{}'::jsonb)
                        || jsonb_build_object(
                            'progress',
                            jsonb_build_object(
                                'stage', 'queued',
                                'message', 'Waiting to restart'
                            )
                        )
                WHERE id = :id AND status = 'running'
                """
            ),
            {"id": assessment_id},
        )
        await db.commit()


async def fail_stale_assessments(session_factory, *, stale_minutes: int) -> int:
    async with session_factory() as db:
        result = await db.execute(
            text(
                """
                UPDATE assessments
                SET status = 'failed',
                    completed_at = NOW(),
                    updated_at = NOW(),
                    metadata = COALESCE(metadata, '{}'::jsonb)
                        || jsonb_build_object(
                            'error', 'The assessment was interrupted. Run it again.',
                            'progress',
                            jsonb_build_object(
                                'stage', 'failed',
                                'message', 'The assessment was interrupted. Run it again.'
                            )
                        )
                WHERE status = 'running'
                  AND updated_at < NOW()
                      - (CAST(:stale_minutes AS integer) * INTERVAL '1 minute')
                """
            ),
            {"stale_minutes": stale_minutes},
        )
        await db.commit()
    return int(result.rowcount or 0)


async def run_assessment_job(session_factory, assessment_id: UUID) -> str:
    """Run one claimed assessment to completion. Returns the final status."""

    async def progress(stage: str, message: str) -> None:
        await set_progress(session_factory, assessment_id, stage, message)

    embedding_service = None
    risk_service = None
    started = time.perf_counter()

    try:
        async with session_factory() as db:
            assessment = await db.get(Assessment, assessment_id)
            if assessment is None:
                return "missing"
            vendor_id = str(assessment.vendor_id)

            embedding_service = EmbeddingService()
            risk_service = RiskAnalysisService(
                retriever=SemanticRetriever(embedding_service)
            )

            analysis = await risk_service.analyze_vendor(
                db=db,
                vendor_id=vendor_id,
                on_progress=progress,
            )

            await progress("saving", "Saving findings")
            await AssessmentPersistenceService().persist(
                db=db,
                analysis=analysis,
                assessment_id=assessment_id,
            )

            await progress("scoring", "Applying risk policy")
            await RiskPolicyService().apply_assessment(
                db=db,
                assessment_id=assessment_id,
            )

            await db.refresh(assessment)
            assessment.status = "completed"
            assessment.completed_at = datetime.now(timezone.utc)
            assessment.extra_data = {
                **(assessment.extra_data or {}),
                "progress": {"stage": "completed", "message": "Completed"},
                "duration_seconds": round(time.perf_counter() - started, 1),
            }
            await db.commit()

        logger.info(
            "Background assessment completed. assessment_id=%s duration=%.1fs",
            assessment_id,
            time.perf_counter() - started,
        )
        return "completed"

    except asyncio.CancelledError:
        # Shutdown: let the assessment resume after restart.
        logger.info(
            "Background assessment interrupted by shutdown; requeuing. "
            "assessment_id=%s",
            assessment_id,
        )
        try:
            await asyncio.shield(requeue(session_factory, assessment_id))
        except Exception:
            logger.exception("Could not requeue assessment %s", assessment_id)
        raise

    except Exception as exc:
        logger.exception(
            "Background assessment failed. assessment_id=%s", assessment_id
        )
        try:
            await mark_failed(session_factory, assessment_id, user_message(exc))
        except Exception:
            logger.exception("Could not mark assessment %s failed", assessment_id)
        return "failed"

    finally:
        if risk_service is not None:
            risk_service.close()
        if embedding_service is not None:
            try:
                await embedding_service.close()
            except Exception:
                logger.warning("Embedding client close failed.", exc_info=True)


def report_assessment_worker_exit(task: asyncio.Task) -> None:
    """Log a crashed assessment worker immediately instead of at shutdown."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.critical(
            "Assessment worker stopped unexpectedly: %s: %s. Queued "
            "assessments will not run until the app restarts.",
            type(exc).__name__,
            exc,
            exc_info=exc,
        )


async def run_assessment_worker() -> None:
    enabled = os.getenv("ASSESSMENT_WORKER_ENABLED", "true").strip().lower()
    if enabled in {"false", "0", "no", "off"}:
        logger.info("Background assessment worker is disabled.")
        return
    if enabled not in {"true", "1", "yes", "on"}:
        raise ValueError("ASSESSMENT_WORKER_ENABLED must be a boolean value.")

    poll_seconds = _int_env("ASSESSMENT_WORKER_POLL_SECONDS", 5, 1)
    stale_minutes = _int_env("ASSESSMENT_STALE_MINUTES", 15, 5)

    engine = create_async_engine(
        get_database_url(),
        poolclass=NullPool,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    next_recovery_at = time.monotonic()

    logger.info(
        "Background assessment worker started. poll=%ss stale=%smin",
        poll_seconds,
        stale_minutes,
    )

    try:
        while True:
            try:
                if time.monotonic() >= next_recovery_at:
                    failed = await fail_stale_assessments(
                        session_factory, stale_minutes=stale_minutes
                    )
                    next_recovery_at = time.monotonic() + 300
                    if failed:
                        logger.warning(
                            "Marked %s interrupted assessment(s) failed.", failed
                        )

                # Clear before claiming so a notification that arrives
                # during the claim is not lost.
                _wake.clear()
                assessment_id = await claim_next_assessment(session_factory)

                if assessment_id is None:
                    try:
                        await asyncio.wait_for(_wake.wait(), timeout=poll_seconds)
                    except TimeoutError:
                        pass
                    continue

                logger.info(
                    "Claimed assessment %s for background processing.",
                    assessment_id,
                )
                await run_assessment_job(session_factory, assessment_id)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Assessment worker loop error. Retrying after poll delay."
                )
                await asyncio.sleep(poll_seconds)
    finally:
        await engine.dispose()
