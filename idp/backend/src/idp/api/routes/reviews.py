from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from idp.api.deps import ContainerDep, SessionDep, require
from idp.application.review import ReviewService
from idp.domain.identity import Permission, Principal

router = APIRouter(prefix="/reviews", tags=["reviews"])

Reader = Annotated[Principal, Depends(require(Permission.REVIEWS_READ))]
Writer = Annotated[Principal, Depends(require(Permission.REVIEWS_WRITE))]


def get_review_service(container: ContainerDep, session: SessionDep) -> ReviewService:
    return ReviewService(
        session, scheduler=container.scheduler, retry_budget=container.settings.job_max_attempts
    )


Reviews = Annotated[ReviewService, Depends(get_review_service)]


class ReviewTaskOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    job_id: uuid.UUID
    status: str
    reasons: list[dict[str, Any]]
    assignee_id: uuid.UUID | None
    created_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None


class FieldActionRequest(BaseModel):
    action: str = Field(pattern="^(accept|edit|reject)$")
    value: Any = None
    reason: str | None = Field(default=None, max_length=1000)


class AddRowRequest(BaseModel):
    part_id: uuid.UUID
    array_path: str = Field(pattern=r"^[a-z][a-z0-9_]*\[\]$")
    cells: dict[str, Any]


class ResolveRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


def _out(task: Any, document: Any) -> ReviewTaskOut:
    return ReviewTaskOut(
        id=task.id,
        document_id=task.document_id,
        document_name=document.original_filename,
        job_id=task.job_id,
        status=task.status,
        reasons=task.reasons,
        assignee_id=task.assignee_id,
        created_at=task.created_at,
        resolved_at=task.resolved_at,
        resolution_note=task.resolution_note,
    )


@router.get("", response_model=list[ReviewTaskOut])
async def list_tasks(
    principal: Reader,
    reviews: Reviews,
    status_filter: Annotated[
        str | None,
        Query(alias="status", pattern="^(open|in_progress|approved|rejected|sent_back)$"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ReviewTaskOut]:
    """Open and in-progress tasks by default (oldest first), or filter by status."""
    return [
        _out(s.task, s.document)
        for s in await reviews.list_tasks(principal, status=status_filter, limit=limit)
    ]


@router.get("/counts")
async def counts(principal: Reader, reviews: Reviews) -> dict[str, int]:
    return await reviews.counts(principal)


@router.get("/{task_id}")
async def get_task(task_id: uuid.UUID, principal: Reader, reviews: Reviews) -> dict[str, Any]:
    """Task, document, parts with fields/tables/validation, and the action history."""
    d = await reviews.detail(principal, task_id)
    payload = {
        "task": _out(d.task, d.document).model_dump(),
        "document": {
            "id": d.document.id,
            "original_filename": d.document.original_filename,
            "page_count": d.document.page_count,
            "status": d.document.status,
        },
        "parts": [
            {
                "part_id": p.part.id,
                "pages": [p.part.page_start, p.part.page_end],
                "document_type": p.doc_type.key if p.doc_type else None,
                "document_type_name": p.doc_type.name if p.doc_type else None,
                "classification_confidence": p.part.classification_confidence,
                "schema_version": p.version.version if p.version else None,
                "fields": p.fields,
                "tables": p.tables,
                **p.sections,
            }
            for p in d.parts
        ],
        "actions": [
            {
                "id": a.id,
                "action": a.action,
                "actor_id": a.actor_id,
                "path": a.path,
                "row_id": a.row_id,
                "original_value": a.original_value,
                "corrected_value": a.corrected_value,
                "reason": a.reason,
                "created_at": a.created_at,
            }
            for a in d.actions
        ],
    }
    result: dict[str, Any] = jsonable_encoder(payload)
    return result


@router.post("/{task_id}/claim", status_code=status.HTTP_204_NO_CONTENT)
async def claim(task_id: uuid.UUID, principal: Writer, reviews: Reviews) -> None:
    await reviews.claim(principal, task_id)


@router.post("/{task_id}/fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def field_action(
    task_id: uuid.UUID,
    field_id: uuid.UUID,
    body: FieldActionRequest,
    principal: Writer,
    reviews: Reviews,
) -> None:
    """Accept, edit (value is normalised and type-checked) or reject a field; validation re-runs."""
    await reviews.field_action(
        principal, task_id, field_id, action=body.action, value=body.value, reason=body.reason
    )


@router.post("/{task_id}/rows", status_code=status.HTTP_201_CREATED)
async def add_row(
    task_id: uuid.UUID, body: AddRowRequest, principal: Writer, reviews: Reviews
) -> dict[str, str]:
    row_id = await reviews.add_row(principal, task_id, body.part_id, body.array_path, body.cells)
    return {"row_id": row_id}


@router.delete("/{task_id}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_row(
    task_id: uuid.UUID,
    row_id: str,
    principal: Writer,
    reviews: Reviews,
    part_id: Annotated[uuid.UUID, Query()],
) -> None:
    await reviews.delete_row(principal, task_id, part_id, row_id)


@router.post("/{task_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve(
    task_id: uuid.UUID, body: ResolveRequest, principal: Writer, reviews: Reviews
) -> None:
    """Approve the document as reviewed; the paused run resumes with its remaining steps."""
    await reviews.approve(principal, task_id, body.note)


@router.post("/{task_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject(
    task_id: uuid.UUID, body: ReasonRequest, principal: Writer, reviews: Reviews
) -> None:
    await reviews.reject(principal, task_id, body.reason)


@router.post("/{task_id}/send-back", status_code=status.HTTP_202_ACCEPTED)
async def send_back(
    task_id: uuid.UUID, body: ReasonRequest, principal: Writer, reviews: Reviews
) -> dict[str, uuid.UUID]:
    """Cancel this run and reprocess the document as a new job."""
    job = await reviews.send_back(principal, task_id, body.reason)
    return {"job_id": job.id}
