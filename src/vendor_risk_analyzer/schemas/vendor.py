from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class VendorCreate(BaseModel):
    name: str = Field(
        min_length=2,
        max_length=255,
    )

    website: str | None = Field(
        default=None,
        max_length=500,
    )


class VendorResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: UUID
    name: str
    website: str | None
    status: str