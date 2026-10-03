from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from idp.domain.lifecycle import DocumentStatus, JobStatus, StepStatus


class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    detected_mime_type: str
    size_bytes: int
    status: DocumentStatus
    page_count: int | None
    received_at: datetime
    source: str


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_number: int
    width: float
    height: float
    unit: str
    rotation: int
    has_text_layer: bool
    char_count: int
    text_source: str | None = None
    ocr_status: str | None = None
    text_quality: float | None = None
    ocr_confidence: float | None = None
    language: str | None = None
    table_density: float | None = None
    word_count: int | None = None
    image_width: int | None = None
    image_height: int | None = None


class DocumentDetail(DocumentSummary):
    declared_mime_type: str | None
    sha256: str
    source: str
    scan_status: str
    uploaded_by_id: uuid.UUID | None
    pages: list[PageOut] = Field(default_factory=list)


class DocumentList(BaseModel):
    items: list[DocumentSummary]
    next_cursor: str | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_key: str
    workflow_version: int
    pipeline_version: str
    trigger: str
    status: JobStatus
    attempts: int
    max_attempts: int
    next_attempt_at: datetime | None
    current_step: str | None
    last_error_category: str | None
    last_error_code: str | None
    last_error_message: str | None
    correlation_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    step_key: str
    attempt: int
    status: StepStatus
    provider: str | None
    provider_version: str | None
    metrics: dict[str, Any]
    error_category: str | None
    error_code: str | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None


class JobWithStepsOut(JobOut):
    steps: list[StepOut]


class StatusChangeOut(BaseModel):
    from_status: DocumentStatus
    to_status: DocumentStatus
    reason: str | None
    actor_type: str
    actor_id: uuid.UUID | None
    occurred_at: datetime


class TimelineOut(BaseModel):
    jobs: list[JobWithStepsOut]
    status_changes: list[StatusChangeOut]


class UploadResponse(BaseModel):
    document: DocumentSummary
    job_id: uuid.UUID


class DownloadLinkOut(BaseModel):
    url: str
    expires_at: datetime


class PageImageOut(BaseModel):
    page_number: int
    url: str
    width: int
    height: int
    expires_at: datetime


class LayoutWordOut(BaseModel):
    text: str
    bbox: list[float]
    confidence: float | None


class LayoutLineOut(BaseModel):
    id: str
    text: str
    bbox: list[float]
    words: list[LayoutWordOut]


class PageLayoutOut(BaseModel):
    page_number: int
    source: str
    text_quality: float
    language: str | None
    ocr_confidence: float | None
    lines: list[LayoutLineOut]
