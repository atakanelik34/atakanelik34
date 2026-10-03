from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    description: str
    is_active: bool
    project_id: uuid.UUID | None
    created_at: datetime


class DocumentTypeSummaryOut(DocumentTypeOut):
    published_version: int | None
    draft_version: int | None


class SchemaVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    status: str
    definition: dict[str, Any]
    created_at: datetime
    published_at: datetime | None


class SchemaVersionSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    status: str
    created_at: datetime
    published_at: datetime | None


class DocumentTypeDetailOut(DocumentTypeOut):
    versions: list[SchemaVersionSummaryOut]


class CreateDocumentTypeRequest(BaseModel):
    key: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    project_key: str | None = None
    definition: dict[str, Any] | None = None
    publish: bool = False


class FromTemplateRequest(BaseModel):
    template_key: str
    key: str | None = Field(default=None, max_length=63)
    publish: bool = False


class UpdateDocumentTypeRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class SaveDraftRequest(BaseModel):
    definition: dict[str, Any] | None = None


class TemplateOut(BaseModel):
    key: str
    name: str
    description: str
    field_count: int
    definition: dict[str, Any]


class DocumentPartOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    part_index: int
    page_start: int
    page_end: int
    document_type_id: uuid.UUID | None
    document_type_key: str | None = None
    document_type_name: str | None = None
    schema_version_id: uuid.UUID | None
    schema_version: int | None = None
    classification_confidence: float
    classifier: str
    classification_reasons: list[str]
    status: str
