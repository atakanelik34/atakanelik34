from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from starlette.datastructures import UploadFile

from idp.api.deps import ContainerDep, SessionDep, require
from idp.api.schemas.documents import (
    DocumentDetail,
    DocumentList,
    DocumentSummary,
    DownloadLinkOut,
    JobOut,
    JobWithStepsOut,
    LayoutLineOut,
    LayoutWordOut,
    PageImageOut,
    PageLayoutOut,
    PageOut,
    StatusChangeOut,
    StepOut,
    TimelineOut,
    UploadResponse,
)
from idp.api.schemas.taxonomy import DocumentPartOut
from idp.application.documents import DocumentService
from idp.application.ingestion import IncomingFile, IngestionService
from idp.application.results import ResultService
from idp.domain.documents import DocumentSource
from idp.domain.errors import LengthRequiredError, PayloadTooLargeError, ValidationError
from idp.domain.identity import Permission, Principal
from idp.domain.lifecycle import DocumentStatus

router = APIRouter(prefix="/documents", tags=["documents"])

Reader = Annotated[Principal, Depends(require(Permission.DOCUMENTS_READ))]
Writer = Annotated[Principal, Depends(require(Permission.DOCUMENTS_WRITE))]

# Allowance for multipart boundaries and the small form fields around the file.
_MULTIPART_OVERHEAD = 64 * 1024

_UPLOAD_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "project_key": {"type": "string"},
                    },
                }
            }
        },
    }
}


def get_document_service(container: ContainerDep, session: SessionDep) -> DocumentService:
    return DocumentService(
        session,
        storage=container.storage,
        scheduler=container.scheduler,
        signed_url_ttl_seconds=container.settings.signed_url_ttl_seconds,
    )


Documents = Annotated[DocumentService, Depends(get_document_service)]


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=_UPLOAD_SCHEMA,
)
async def upload(
    request: Request, principal: Writer, container: ContainerDep, session: SessionDep
) -> UploadResponse:
    """Upload one document (multipart field `file`). Processing starts asynchronously."""
    # Reject oversized bodies before the multipart parser spools them to disk.
    max_bytes = container.settings.max_upload_bytes
    length = request.headers.get("content-length")
    if length is None:
        raise LengthRequiredError("Content-Length header is required for uploads")
    if not length.isdigit() or int(length) > max_bytes + _MULTIPART_OVERHEAD:
        raise PayloadTooLargeError(f"File exceeds the {max_bytes // (1024 * 1024)} MiB limit")

    async with request.form(max_files=1, max_fields=4) as form:
        upload_file = form.get("file")
        if not isinstance(upload_file, UploadFile):
            raise ValidationError("Multipart field 'file' is required")
        project_key = form.get("project_key")
        result = await IngestionService(
            session,
            storage=container.storage,
            scanner=container.scanner,
            scheduler=container.scheduler,
            max_upload_bytes=max_bytes,
        ).ingest(
            principal,
            IncomingFile(
                stream=upload_file.file,
                filename=upload_file.filename,
                declared_mime_type=upload_file.content_type,
                source=DocumentSource.API if principal.api_key_id else DocumentSource.WEB_UPLOAD,
                project_key=project_key if isinstance(project_key, str) else None,
            ),
        )
    return UploadResponse(
        document=DocumentSummary.model_validate(result.document), job_id=result.job.id
    )


@router.get("", response_model=DocumentList)
async def list_documents(
    principal: Reader,
    documents: Documents,
    status_filter: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    source: Annotated[DocumentSource | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> DocumentList:
    page = await documents.list(
        principal, status=status_filter, limit=limit, cursor=cursor, source=source
    )
    return DocumentList(
        items=[DocumentSummary.model_validate(d) for d in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: uuid.UUID, principal: Reader, documents: Documents
) -> DocumentDetail:
    document = await documents.get(principal, document_id)
    pages = await documents.pages(document)
    detail = DocumentDetail.model_validate(document)
    detail.pages = [PageOut.model_validate(p) for p in pages]
    return detail


@router.get("/{document_id}/timeline", response_model=TimelineOut)
async def get_timeline(
    document_id: uuid.UUID, principal: Reader, documents: Documents
) -> TimelineOut:
    timeline = await documents.timeline(principal, document_id)
    return TimelineOut(
        jobs=[
            JobWithStepsOut(
                **JobOut.model_validate(item.job).model_dump(),
                steps=[StepOut.model_validate(s) for s in item.steps],
            )
            for item in timeline.jobs
        ],
        status_changes=[
            StatusChangeOut(
                from_status=DocumentStatus((change.before or {})["status"]),
                to_status=DocumentStatus((change.after or {})["status"]),
                reason=(change.after or {}).get("reason"),
                actor_type=change.actor_type,
                actor_id=change.actor_id,
                occurred_at=change.occurred_at,
            )
            for change in timeline.status_changes
        ],
    )


@router.get("/{document_id}/pages/{number}/image", response_model=PageImageOut)
async def page_image(
    document_id: uuid.UUID,
    number: Annotated[int, Path(ge=1)],
    principal: Reader,
    documents: Documents,
) -> PageImageOut:
    """Signed URL to the rendered page image used by the document viewer."""
    page, link = await documents.page_image(principal, document_id, number)
    return PageImageOut(
        page_number=page.page_number,
        url=link.url,
        width=page.image_width or 0,
        height=page.image_height or 0,
        expires_at=link.expires_at,
    )


@router.get("/{document_id}/pages/{number}/layout", response_model=PageLayoutOut)
async def page_layout(
    document_id: uuid.UUID,
    number: Annotated[int, Path(ge=1)],
    principal: Reader,
    documents: Documents,
) -> PageLayoutOut:
    """Words and lines with normalised bounding boxes (origin top-left, 0..1)."""
    layout = await documents.page_layout(principal, document_id, number)
    return PageLayoutOut(
        page_number=layout.page_number,
        source=layout.source.value,
        text_quality=layout.text_quality,
        language=layout.language,
        ocr_confidence=layout.ocr_confidence,
        lines=[
            LayoutLineOut(
                id=line.id,
                text=line.text,
                bbox=line.bbox.as_list(),
                words=[
                    LayoutWordOut(text=w.text, bbox=w.bbox.as_list(), confidence=w.confidence)
                    for w in line.words
                ],
            )
            for line in layout.lines
        ],
    )


@router.get("/{document_id}/extraction")
async def get_extraction(
    document_id: uuid.UUID, principal: Reader, session: SessionDep
) -> dict[str, Any]:
    """The processing result: parts, classification, fields with confidence and
    provenance, line-item tables, validation, enrichment, actions, metrics, errors."""
    result: dict[str, Any] = jsonable_encoder(
        await ResultService(session).processing_result(principal, document_id)
    )
    return result


@router.get("/{document_id}/parts", response_model=list[DocumentPartOut])
async def list_parts(
    document_id: uuid.UUID,
    principal: Reader,
    documents: Documents,
    job_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[DocumentPartOut]:
    """Logical documents found in the file (classification + splitting)."""
    rows = await documents.parts(principal, document_id, job_id)
    out = []
    for part, doc_type, version in rows:
        item = DocumentPartOut.model_validate(part)
        if doc_type is not None:
            item.document_type_key, item.document_type_name = doc_type.key, doc_type.name
        if version is not None:
            item.schema_version = version.version
        out.append(item)
    return out


@router.get("/{document_id}/download", response_model=DownloadLinkOut)
async def download_link(
    document_id: uuid.UUID, principal: Reader, documents: Documents
) -> DownloadLinkOut:
    """Short-lived signed URL to the original file. The API never proxies document bytes."""
    link = await documents.download_link(principal, document_id)
    return DownloadLinkOut(url=link.url, expires_at=link.expires_at)


@router.post("/{document_id}/process", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def request_processing(
    document_id: uuid.UUID, principal: Writer, documents: Documents
) -> JobOut:
    """Reprocess a finished document, or replay a failed one, as a new job."""
    job = await documents.request_processing(principal, document_id)
    return JobOut.model_validate(job)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, principal: Writer, documents: Documents
) -> Response:
    await documents.delete(principal, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
