from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from idp.api.deps import SessionDep, require
from idp.api.schemas.taxonomy import (
    CreateDocumentTypeRequest,
    DocumentTypeDetailOut,
    DocumentTypeOut,
    DocumentTypeSummaryOut,
    FromTemplateRequest,
    SaveDraftRequest,
    SchemaVersionOut,
    SchemaVersionSummaryOut,
    TemplateOut,
    UpdateDocumentTypeRequest,
)
from idp.application.taxonomy import TaxonomyService
from idp.domain.identity import Permission, Principal
from idp.domain.templates import TEMPLATES

router = APIRouter(tags=["taxonomy"])

ConfigReader = Annotated[Principal, Depends(require(Permission.CONFIG_READ))]
ConfigWriter = Annotated[Principal, Depends(require(Permission.CONFIG_WRITE))]


@router.get("/document-type-templates", response_model=list[TemplateOut])
async def list_templates(_principal: ConfigReader) -> list[TemplateOut]:
    return [
        TemplateOut(
            key=t.key,
            name=t.name,
            description=t.description,
            field_count=len(t.definition.flatten()),
            definition=t.definition.model_dump(mode="json"),
        )
        for t in TEMPLATES.values()
    ]


@router.get("/document-types", response_model=list[DocumentTypeSummaryOut])
async def list_types(principal: ConfigReader, session: SessionDep) -> list[DocumentTypeSummaryOut]:
    summaries = await TaxonomyService(session).list_types(principal)
    return [
        DocumentTypeSummaryOut(
            **DocumentTypeOut.model_validate(s.type).model_dump(),
            published_version=s.published_version,
            draft_version=s.draft_version,
        )
        for s in summaries
    ]


async def _detail(
    service: TaxonomyService, principal: Principal, type_id: uuid.UUID
) -> DocumentTypeDetailOut:
    detail = await service.get_type(principal, type_id)
    return DocumentTypeDetailOut(
        **DocumentTypeOut.model_validate(detail.type).model_dump(),
        versions=[SchemaVersionSummaryOut.model_validate(v) for v in detail.versions],
    )


@router.post(
    "/document-types", response_model=DocumentTypeDetailOut, status_code=status.HTTP_201_CREATED
)
async def create_type(
    body: CreateDocumentTypeRequest, principal: ConfigWriter, session: SessionDep
) -> DocumentTypeDetailOut:
    service = TaxonomyService(session)
    created = await service.create_type(
        principal,
        key=body.key,
        name=body.name,
        description=body.description,
        project_key=body.project_key,
        definition=body.definition,
        publish=body.publish,
    )
    return await _detail(service, principal, created.id)


@router.post(
    "/document-types/from-template",
    response_model=DocumentTypeDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_from_template(
    body: FromTemplateRequest, principal: ConfigWriter, session: SessionDep
) -> DocumentTypeDetailOut:
    service = TaxonomyService(session)
    created = await service.create_from_template(
        principal, template_key=body.template_key, key=body.key, publish=body.publish
    )
    return await _detail(service, principal, created.id)


@router.get("/document-types/{type_id}", response_model=DocumentTypeDetailOut)
async def get_type(
    type_id: uuid.UUID, principal: ConfigReader, session: SessionDep
) -> DocumentTypeDetailOut:
    return await _detail(TaxonomyService(session), principal, type_id)


@router.patch("/document-types/{type_id}", response_model=DocumentTypeDetailOut)
async def update_type(
    type_id: uuid.UUID,
    body: UpdateDocumentTypeRequest,
    principal: ConfigWriter,
    session: SessionDep,
) -> DocumentTypeDetailOut:
    service = TaxonomyService(session)
    await service.update_type(
        principal, type_id, name=body.name, description=body.description, is_active=body.is_active
    )
    return await _detail(service, principal, type_id)


@router.get("/document-types/{type_id}/schemas/{version}", response_model=SchemaVersionOut)
async def get_version(
    type_id: uuid.UUID,
    version: Annotated[int, Path(ge=1)],
    principal: ConfigReader,
    session: SessionDep,
) -> SchemaVersionOut:
    return SchemaVersionOut.model_validate(
        await TaxonomyService(session).get_version(principal, type_id, version)
    )


@router.put("/document-types/{type_id}/draft", response_model=SchemaVersionOut)
async def save_draft(
    type_id: uuid.UUID, body: SaveDraftRequest, principal: ConfigWriter, session: SessionDep
) -> SchemaVersionOut:
    """Create or replace the draft. Omit `definition` to start from the published version."""
    draft = await TaxonomyService(session).save_draft(principal, type_id, body.definition)
    return SchemaVersionOut.model_validate(draft)


@router.post("/document-types/{type_id}/publish", response_model=SchemaVersionOut)
async def publish(
    type_id: uuid.UUID, principal: ConfigWriter, session: SessionDep
) -> SchemaVersionOut:
    """Publish the draft. The previous published version is retired (kept for history)."""
    return SchemaVersionOut.model_validate(
        await TaxonomyService(session).publish(principal, type_id)
    )
