from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.enums import AuthMode, SourceStatus, SourceType


class SourceCreate(BaseModel):
    source_type: SourceType
    provider: str | None = None
    status: SourceStatus = SourceStatus.PLANNED
    auth_mode: AuthMode = AuthMode.NONE
    original_url: str = Field(..., min_length=1)
    normalized_url: str | None = None
    notes: str | None = None

    @field_validator("original_url", "normalized_url", mode="before")
    @classmethod
    def strip_url(cls, v: str | None) -> str | None:
        return v.strip() if v else v


class SourceUpdate(BaseModel):
    source_type: SourceType | None = None
    provider: str | None = None
    status: SourceStatus | None = None
    auth_mode: AuthMode | None = None
    normalized_url: str | None = None
    notes: str | None = None


class SourceRead(BaseModel):
    source_id: str
    source_type: SourceType
    provider: str | None
    status: SourceStatus
    auth_mode: AuthMode
    original_url: str
    normalized_url: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
