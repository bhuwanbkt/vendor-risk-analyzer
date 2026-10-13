import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from vendor_risk_analyzer.audit.service import record as audit
from vendor_risk_analyzer.auth.access import require_vendor_access

from vendor_risk_analyzer.auth.dependencies import (
    require_roles,
    verify_csrf,
)
from vendor_risk_analyzer.db.models import (
    Document,
)
from vendor_risk_analyzer.db.session import get_db
from vendor_risk_analyzer.schemas.document import (
    DocumentResponse,
    DocumentUploadRequest,
    DocumentUploadResponse,
    STALE_PROCESSING_AFTER,
)
from vendor_risk_analyzer.storage.client import (
    generate_upload_url,
    get_object_metadata,
)
from vendor_risk_analyzer.embeddings.worker import notify_new_document
from vendor_risk_analyzer.ingestion.service import (
    ingest_document,
)


router = APIRouter(
    prefix="/api/vendors",
    tags=["Documents"],
)


# Statuses from which a document may be (re)processed.
REINGESTABLE_STATUSES = (
    "uploaded",
    "parsed",
    "failed",
    "embedding_pending",
    "embedding_failed",
    "ready",
)


def safe_filename(filename: str) -> str:
    filename = Path(filename).name

    return re.sub(
        r"[^A-Za-z0-9._-]",
        "_",
        filename,
    )


@router.post(
    "/{vendor_id}/documents/upload-url",
    response_model=DocumentUploadResponse,
)
async def create_upload_url(
    vendor_id: UUID,
    payload: DocumentUploadRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(
        require_roles(
            "analyst",
            "admin",
        )
    ),
    _: None = Depends(verify_csrf),
):
    access = await require_vendor_access(db, user, vendor_id)

    existing = (
        await db.execute(
            select(Document).where(
                Document.vendor_id == vendor_id,
                Document.sha256 == payload.sha256,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        if (
            existing.status != "upload_pending"
            or existing.size_bytes != payload.size_bytes
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This document has already been uploaded",
            )

        # Resume an upload that was never completed (closed tab,
        # network error, expired URL): issue a fresh URL for the
        # same record instead of blocking this file forever.
        upload_url = generate_upload_url(
            object_key=existing.object_key,
            content_type=payload.content_type,
            expires_in=300,
        )

        existing.mime_type = payload.content_type

        await db.commit()

        await audit(
            access,
            user,
            "document.upload_requested",
            target_type="document",
            target_id=existing.id,
            vendor_id=vendor_id,
            details={
                "filename": existing.filename,
                "size_bytes": existing.size_bytes,
                "resumed": True,
            },
        )

        return DocumentUploadResponse(
            document_id=existing.id,
            upload_url=upload_url,
            object_key=existing.object_key,
            expires_in=300,
        )

    document_id = uuid4()

    filename = safe_filename(
        payload.filename
    )

    extension = (
        filename
        .rsplit(".", 1)[-1]
        .lower()
    )

    object_key = (
        f"vendors/{vendor_id}/"
        f"raw/{document_id}/"
        f"{filename}"
    )

    upload_url = generate_upload_url(
        object_key=object_key,
        content_type=payload.content_type,
        expires_in=300,
    )

    document = Document(
        id=document_id,
        vendor_id=vendor_id,
        filename=filename,
        object_key=object_key,
        file_type=extension,
        mime_type=payload.content_type,
        size_bytes=payload.size_bytes,
        sha256=payload.sha256,
        status="upload_pending",
    )

    db.add(document)

    try:
        await db.commit()

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document already exists",
        ) from exc

    await audit(
        access,
        user,
        "document.upload_requested",
        target_type="document",
        target_id=document_id,
        vendor_id=vendor_id,
        details={
            "filename": filename,
            "size_bytes": payload.size_bytes,
        },
    )

    return DocumentUploadResponse(
        document_id=document_id,
        upload_url=upload_url,
        object_key=object_key,
        expires_in=300,
    )


@router.post(
    "/{vendor_id}/documents/{document_id}/complete",
    response_model=DocumentResponse,
)
async def complete_upload(
    vendor_id: UUID,
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(
        require_roles(
            "analyst",
            "admin",
        )
    ),
    _: None = Depends(verify_csrf),
):
    access = await require_vendor_access(db, user, vendor_id)
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.vendor_id == vendor_id,
        )
    )

    document = result.scalar_one_or_none()

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    metadata = await asyncio.to_thread(
        get_object_metadata,
        document.object_key,
    )

    if metadata is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Uploaded object was not found",
        )

    actual_size = metadata.get(
        "ContentLength"
    )

    if (
        document.size_bytes is not None
        and actual_size != document.size_bytes
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Uploaded file size does not match",
        )

    document.status = "uploaded"

    document.extra_data = {
        **document.extra_data,
        "etag": metadata.get("ETag"),
    }

    await db.commit()
    await db.refresh(document)

    await audit(
        access,
        user,
        "document.uploaded",
        target_type="document",
        target_id=document.id,
        vendor_id=vendor_id,
        details={"filename": document.filename},
    )

    return document


@router.get(
    "/{vendor_id}/documents",
    response_model=list[DocumentResponse],
)
async def list_documents(
    vendor_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(
        require_roles(
            "viewer",
            "analyst",
            "admin",
        )
    ),
):
    access = await require_vendor_access(db, user, vendor_id)
    result = await db.execute(
        select(Document)
        .where(
            Document.vendor_id == vendor_id
        )
        .order_by(
            Document.created_at.desc()
        )
    )

    return result.scalars().all()


@router.post(
    "/{vendor_id}/documents/{document_id}/ingest",
    response_model=DocumentResponse,
)
async def ingest_uploaded_document(
    vendor_id: UUID,
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(
        require_roles(
            "analyst",
            "admin",
        )
    ),
    _: None = Depends(verify_csrf),
):
    access = await require_vendor_access(db, user, vendor_id)
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.vendor_id == vendor_id,
        )
    )

    document = result.scalar_one_or_none()

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    # Claim the document atomically: the status check and the
    # switch to "processing" are one conditional UPDATE, so two
    # simultaneous requests (or a request racing the embedding
    # worker's claim) cannot both start processing.
    now = datetime.now(timezone.utc)

    claim = await db.execute(
        update(Document)
        .where(
            Document.id == document_id,
            Document.vendor_id == vendor_id,
            or_(
                Document.status.in_(
                    REINGESTABLE_STATUSES
                ),
                and_(
                    Document.status == "processing",
                    Document.updated_at
                    < now - STALE_PROCESSING_AFTER,
                ),
            ),
        )
        .values(
            status="processing",
            updated_at=now,
        )
        .execution_options(
            synchronize_session=False,
        )
    )

    if claim.rowcount != 1:
        await db.rollback()

        raise HTTPException(
            status_code=409,
            detail=(
                "Document is not ready "
                "for ingestion"
            ),
        )

    await audit(
        access,
        user,
        "document.processing_started",
        target_type="document",
        target_id=document_id,
        vendor_id=vendor_id,
        details={"filename": document.filename},
    )

    try:
        result = await ingest_document(
            document=document,
            db=db,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Document ingestion failed",
        ) from exc

    # Start embedding now instead of at the worker's next idle poll.
    notify_new_document()

    return result
