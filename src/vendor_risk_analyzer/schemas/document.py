from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
)


MAX_FILE_SIZE = 25 * 1024 * 1024

# A document still "processing" after this long was interrupted
# (crash, restart, or a failed error handler) and may be retried.
STALE_PROCESSING_AFTER = timedelta(minutes=15)


def is_stale_processing(
    status: str,
    updated_at: datetime | None,
) -> bool:
    if status != "processing" or updated_at is None:
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return updated_at < datetime.now(timezone.utc) - STALE_PROCESSING_AFTER

ALLOWED_EXTENSIONS = {
    "pdf",
    "docx",
    "xlsx",
    "csv",
    "txt",
    "md",
}


class DocumentUploadRequest(BaseModel):
    filename: str = Field(
        min_length=1,
        max_length=500,
    )

    content_type: str = Field(
        min_length=1,
        max_length=255,
    )

    size_bytes: int = Field(
        gt=0,
        le=MAX_FILE_SIZE,
    )

    sha256: str = Field(
        min_length=64,
        max_length=64,
    )

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        value = value.lower()

        if not all(
            char in "0123456789abcdef"
            for char in value
        ):
            raise ValueError(
                "SHA256 must contain hexadecimal characters"
            )

        return value

    @field_validator("filename")
    @classmethod
    def validate_extension(cls, value: str) -> str:
        extension = value.rsplit(".", 1)[-1].lower()

        if extension not in ALLOWED_EXTENSIONS:
            raise ValueError(
                "Unsupported document type"
            )

        return value


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    upload_url: str
    object_key: str
    expires_in: int


class DocumentResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: UUID
    vendor_id: UUID
    filename: str
    file_type: str
    mime_type: str | None
    size_bytes: int | None
    status: str
    updated_at: datetime | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def stale_processing(self) -> bool:
        return is_stale_processing(self.status, self.updated_at)
