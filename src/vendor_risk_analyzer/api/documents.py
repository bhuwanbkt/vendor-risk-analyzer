import asyncio
import re
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from vendor_risk_analyzer.auth.dependencies import (
    require_roles,
    verify_csrf,
)
from vendor_risk_analyzer.db.models import (
    Document,
    Vendor,
)
from vendor_risk_analyzer.db.session import get_db
from vendor_risk_analyzer.schemas.document import (
    DocumentResponse,
    DocumentUploadRequest,
    DocumentUploadResponse,
)
from vendor_risk_analyzer.storage.client import (
    generate_upload_url,
    get_object_metadata,
)
from vendor_risk_analyzer.ingestion.service import (
    ingest_document,
)


router = APIRouter(
    prefix="/api/vendors",
    tags=["Documents"],
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
    vendor = await db.get(
        Vendor,
        vendor_id,
    )

    if vendor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor not found",
        )

    existing = await db.execute(
        select(Document).where(
            Document.vendor_id == vendor_id,
            Document.sha256 == payload.sha256,
        )
    )

    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This document has already been uploaded",
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

    if document.status not in {
        "uploaded",
        "parsed",
        "failed",
        "embedding_pending",
        "embedding_failed",
        "ready",
    }:
        raise HTTPException(
            status_code=409,
            detail=(
                "Document is not ready "
                "for ingestion"
            ),
        )

    try:
        return await ingest_document(
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