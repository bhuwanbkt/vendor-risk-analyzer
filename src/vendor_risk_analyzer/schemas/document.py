from uuid import UUID

from pydantic import BaseModel, Field, field_validator, ConfigDict


MAX_FILE_SIZE = 25 * 1024 * 1024

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