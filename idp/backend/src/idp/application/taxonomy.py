"""Taxonomy use cases: document types and their versioned schemas."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pydantic
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.audit import AuditAction, AuditEntity, record_audit
from idp.domain.classification import TypeCandidate
from idp.domain.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from idp.domain.identity import Permission, Principal
from idp.domain.taxonomy import NAME_PATTERN, SchemaDefinition, SchemaStatus
from idp.domain.templates import TEMPLATES
from idp.infrastructure.db.models import DocumentType, Project, SchemaField, SchemaVersion


def parse_definition(raw: dict[str, Any]) -> SchemaDefinition:
    try:
        return SchemaDefinition.model_validate(raw)
    except pydantic.ValidationError as exc:
        errors = [{"loc": [str(part) for part in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
        raise ValidationError("Schema definition is invalid", details={"errors": errors}) from exc


@dataclass(frozen=True, slots=True)
class TypeSummary:
    type: DocumentType
    published_version: int | None
    draft_version: int | None


@dataclass(frozen=True, slots=True)
class TypeDetail:
    type: DocumentType
    versions: Sequence[SchemaVersion]


class TaxonomyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _require(principal: Principal, permission: Permission) -> None:
        if not principal.has(permission):
            raise AuthorizationError(f"Missing permission '{permission.value}'")

    async def _type(self, principal: Principal, type_id: uuid.UUID) -> DocumentType:
        doc_type = await self._session.scalar(
            select(DocumentType).where(
                DocumentType.id == type_id,
                DocumentType.tenant_id == principal.tenant_id,
                DocumentType.deleted_at.is_(None),
            )
        )
        if doc_type is None:
            raise NotFoundError("Document type not found")
        return doc_type

    async def _versions(self, type_id: uuid.UUID) -> Sequence[SchemaVersion]:
        return (
            await self._session.scalars(
                select(SchemaVersion)
                .where(SchemaVersion.document_type_id == type_id)
                .order_by(SchemaVersion.version.desc())
            )
        ).all()

    async def list_types(self, principal: Principal) -> list[TypeSummary]:
        self._require(principal, Permission.CONFIG_READ)
        types = (
            await self._session.scalars(
                select(DocumentType)
                .where(
                    DocumentType.tenant_id == principal.tenant_id,
                    DocumentType.deleted_at.is_(None),
                )
                .order_by(DocumentType.name)
            )
        ).all()
        versions = (
            await self._session.scalars(
                select(SchemaVersion).where(
                    SchemaVersion.document_type_id.in_([t.id for t in types]),
                    SchemaVersion.status.in_([SchemaStatus.PUBLISHED, SchemaStatus.DRAFT]),
                )
            )
        ).all()
        published = {v.document_type_id: v.version for v in versions if v.status == "published"}
        drafts = {v.document_type_id: v.version for v in versions if v.status == "draft"}
        return [TypeSummary(t, published.get(t.id), drafts.get(t.id)) for t in types]

    async def get_type(self, principal: Principal, type_id: uuid.UUID) -> TypeDetail:
        self._require(principal, Permission.CONFIG_READ)
        doc_type = await self._type(principal, type_id)
        return TypeDetail(type=doc_type, versions=await self._versions(type_id))

    async def get_version(
        self, principal: Principal, type_id: uuid.UUID, version: int
    ) -> SchemaVersion:
        self._require(principal, Permission.CONFIG_READ)
        await self._type(principal, type_id)
        row = await self._session.scalar(
            select(SchemaVersion).where(
                SchemaVersion.document_type_id == type_id, SchemaVersion.version == version
            )
        )
        if row is None:
            raise NotFoundError("Schema version not found")
        return row

    async def _project_id(self, principal: Principal, project_key: str | None) -> uuid.UUID | None:
        if project_key is None:
            return None
        project_id = await self._session.scalar(
            select(Project.id).where(
                Project.tenant_id == principal.tenant_id,
                Project.key == project_key,
                Project.deleted_at.is_(None),
            )
        )
        if project_id is None:
            raise NotFoundError("Project not found")
        return project_id

    async def create_type(
        self,
        principal: Principal,
        *,
        key: str,
        name: str,
        description: str = "",
        project_key: str | None = None,
        definition: dict[str, Any] | None = None,
        publish: bool = False,
    ) -> DocumentType:
        self._require(principal, Permission.CONFIG_WRITE)
        if re.fullmatch(NAME_PATTERN, key) is None:
            raise ValidationError("Key must be lowercase letters, digits and underscores")
        parsed = parse_definition(definition or {})
        doc_type = DocumentType(
            tenant_id=principal.tenant_id,
            project_id=await self._project_id(principal, project_key),
            key=key,
            name=name.strip(),
            description=description,
            created_by_id=principal.user_id,
        )
        self._session.add(doc_type)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError(f"A document type with key '{key}' already exists") from exc
        self._session.add(
            SchemaVersion(
                tenant_id=principal.tenant_id,
                document_type_id=doc_type.id,
                version=1,
                status=SchemaStatus.DRAFT,
                definition=parsed.model_dump(mode="json"),
                created_by_id=principal.user_id,
            )
        )
        await self._session.flush()
        record_audit(
            self._session,
            action=AuditAction.DOCUMENT_TYPE_CREATED,
            entity_type=AuditEntity.DOCUMENT_TYPE,
            entity_id=doc_type.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            after={"key": key, "name": doc_type.name},
        )
        if publish:
            await self._publish(principal, doc_type)
        await self._session.commit()
        return doc_type

    async def create_from_template(
        self, principal: Principal, *, template_key: str, key: str | None, publish: bool
    ) -> DocumentType:
        template = TEMPLATES.get(template_key)
        if template is None:
            raise NotFoundError("Template not found")
        return await self.create_type(
            principal,
            key=key or template.key,
            name=template.name,
            description=template.description,
            definition=template.definition.model_dump(mode="json"),
            publish=publish,
        )

    async def update_type(
        self,
        principal: Principal,
        type_id: uuid.UUID,
        *,
        name: str | None,
        description: str | None,
        is_active: bool | None,
    ) -> DocumentType:
        self._require(principal, Permission.CONFIG_WRITE)
        doc_type = await self._type(principal, type_id)
        before = {"name": doc_type.name, "is_active": doc_type.is_active}
        if name is not None:
            doc_type.name = name.strip()
        if description is not None:
            doc_type.description = description
        if is_active is not None:
            doc_type.is_active = is_active
        record_audit(
            self._session,
            action=AuditAction.DOCUMENT_TYPE_UPDATED,
            entity_type=AuditEntity.DOCUMENT_TYPE,
            entity_id=doc_type.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            before=before,
            after={"name": doc_type.name, "is_active": doc_type.is_active},
        )
        await self._session.commit()
        return doc_type

    async def save_draft(
        self, principal: Principal, type_id: uuid.UUID, definition: dict[str, Any] | None
    ) -> SchemaVersion:
        """Create or replace the draft. Without a definition, the draft copies the
        latest published version (the usual way to start an edit)."""
        self._require(principal, Permission.CONFIG_WRITE)
        doc_type = await self._type(principal, type_id)
        versions = await self._versions(type_id)
        draft = next((v for v in versions if v.status == SchemaStatus.DRAFT), None)
        if definition is None:
            published = next((v for v in versions if v.status == SchemaStatus.PUBLISHED), None)
            definition = published.definition if published else {}
        parsed = parse_definition(definition)
        if draft is None:
            draft = SchemaVersion(
                tenant_id=principal.tenant_id,
                document_type_id=doc_type.id,
                version=(versions[0].version + 1) if versions else 1,
                status=SchemaStatus.DRAFT,
                definition=parsed.model_dump(mode="json"),
                created_by_id=principal.user_id,
            )
            self._session.add(draft)
        else:
            draft.definition = parsed.model_dump(mode="json")
        await self._session.flush()
        record_audit(
            self._session,
            action=AuditAction.SCHEMA_DRAFT_SAVED,
            entity_type=AuditEntity.DOCUMENT_TYPE,
            entity_id=doc_type.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            after={"version": draft.version, "fields": len(parsed.flatten())},
        )
        await self._session.commit()
        await self._session.refresh(draft)
        return draft

    async def _publish(self, principal: Principal, doc_type: DocumentType) -> SchemaVersion:
        versions = await self._versions(doc_type.id)
        draft = next((v for v in versions if v.status == SchemaStatus.DRAFT), None)
        if draft is None:
            raise ConflictError("There is no draft to publish")
        parsed = parse_definition(draft.definition)
        await self._session.execute(
            update(SchemaVersion)
            .where(
                SchemaVersion.document_type_id == doc_type.id,
                SchemaVersion.status == SchemaStatus.PUBLISHED,
            )
            .values(status=SchemaStatus.RETIRED)
        )
        await self._session.flush()
        draft.status = SchemaStatus.PUBLISHED
        draft.published_at = datetime.now(UTC)
        draft.published_by_id = principal.user_id
        self._session.add_all(
            SchemaField(
                tenant_id=principal.tenant_id,
                schema_version_id=draft.id,
                path=path,
                type=definition.type.value,
                required=definition.required,
                confidence_threshold=definition.confidence_threshold,
            )
            for path, definition in parsed.flatten()
        )
        await self._session.flush()
        record_audit(
            self._session,
            action=AuditAction.SCHEMA_PUBLISHED,
            entity_type=AuditEntity.DOCUMENT_TYPE,
            entity_id=doc_type.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            after={"version": draft.version},
        )
        return draft

    async def publish(self, principal: Principal, type_id: uuid.UUID) -> SchemaVersion:
        self._require(principal, Permission.CONFIG_WRITE)
        doc_type = await self._type(principal, type_id)
        version = await self._publish(principal, doc_type)
        await self._session.commit()
        await self._session.refresh(version)
        return version


async def published_candidates(
    session: AsyncSession, *, tenant_id: uuid.UUID, project_id: uuid.UUID
) -> list[TypeCandidate]:
    """Active types visible to the project, each with its current published schema."""
    rows = (
        await session.execute(
            select(DocumentType, SchemaVersion)
            .join(SchemaVersion, SchemaVersion.document_type_id == DocumentType.id)
            .where(
                DocumentType.tenant_id == tenant_id,
                DocumentType.deleted_at.is_(None),
                DocumentType.is_active.is_(True),
                or_(DocumentType.project_id.is_(None), DocumentType.project_id == project_id),
                SchemaVersion.status == SchemaStatus.PUBLISHED,
            )
            .order_by(DocumentType.key)
        )
    ).all()
    candidates = []
    for doc_type, version in rows:
        definition = SchemaDefinition.model_validate(version.definition)
        if definition.is_classifiable:
            candidates.append(
                TypeCandidate(
                    document_type_id=doc_type.id,
                    key=doc_type.key,
                    schema_version_id=version.id,
                    rules=definition.classification,
                )
            )
    return candidates
