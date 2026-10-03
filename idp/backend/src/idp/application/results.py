"""Assembles the stable processing-result contract (ARCHITECTURE.md §9).

Every stage contributes to one shape: parts -> classification, fields (with
confidence, threshold and provenance), line-item tables, validation,
enrichment and actions. Later phases fill their sections; the shape is stable.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idp.domain.errors import NotFoundError
from idp.domain.identity import Principal
from idp.domain.taxonomy import FieldDefinition, SchemaDefinition
from idp.infrastructure.db.models import (
    Document,
    DocumentPage,
    DocumentPart,
    DocumentType,
    ExtractedField,
    ExtractionResult,
    ProcessingJob,
    ProcessingStep,
    SchemaVersion,
)


def field_view(f: ExtractedField, definition: FieldDefinition | None) -> dict[str, Any]:
    threshold = definition.confidence_threshold if definition else 0.8
    return {
        "id": str(f.id),
        "path": f.path,
        "row_id": f.row_id,
        "value": f.value,
        "original_value": f.original_value,
        "raw_text": f.raw_text,
        "confidence": f.confidence,
        "threshold": threshold,
        "below_threshold": f.status in ("extracted", "missing") and f.confidence < threshold,
        "required": bool(definition and definition.required),
        "type": definition.type.value if definition else None,
        "status": f.status,
        "normalized": f.normalized,
        "method": f.method,
        "provider": f.provider,
        "provenance": f.provenance,
        "alternatives": f.alternatives,
    }


@dataclass(slots=True)
class PartResult:
    part: DocumentPart
    doc_type: DocumentType | None
    version: SchemaVersion | None
    result: ExtractionResult | None
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    sections: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {"validation": [], "enrichment": [], "actions": []}
    )


class ResultService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _document(self, principal: Principal, document_id: uuid.UUID) -> Document:
        document = await self._session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.tenant_id == principal.tenant_id,
                Document.deleted_at.is_(None),
            )
        )
        if document is None:
            raise NotFoundError("Document not found")
        return document

    async def _job_with_parts(self, document: Document) -> ProcessingJob | None:
        return await self._session.scalar(
            select(ProcessingJob)
            .join(DocumentPart, DocumentPart.job_id == ProcessingJob.id)
            .where(ProcessingJob.document_id == document.id)
            .order_by(ProcessingJob.created_at.desc())
            .limit(1)
        )

    async def part_results(self, job_id: uuid.UUID) -> list[PartResult]:
        rows = (
            await self._session.execute(
                select(DocumentPart, DocumentType, SchemaVersion, ExtractionResult)
                .outerjoin(DocumentType, DocumentType.id == DocumentPart.document_type_id)
                .outerjoin(SchemaVersion, SchemaVersion.id == DocumentPart.schema_version_id)
                .outerjoin(
                    ExtractionResult,
                    (ExtractionResult.part_id == DocumentPart.id)
                    & (ExtractionResult.job_id == DocumentPart.job_id),
                )
                .where(DocumentPart.job_id == job_id)
                .order_by(DocumentPart.part_index)
            )
        ).all()
        results = [PartResult(part, t, v, r) for part, t, v, r in rows]
        result_ids = [r.result.id for r in results if r.result]
        fields = (
            await self._session.scalars(
                select(ExtractedField)
                .where(ExtractedField.result_id.in_(result_ids))
                .order_by(ExtractedField.created_at, ExtractedField.path)
            )
        ).all()
        by_part: dict[uuid.UUID, list[ExtractedField]] = defaultdict(list)
        for f in fields:
            by_part[f.part_id].append(f)
        for r in results:
            schema = SchemaDefinition.model_validate(r.version.definition) if r.version else None
            definitions = dict(schema.flatten()) if schema else {}
            rows_by_array: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
            for f in by_part.get(r.part.id, []):
                view = field_view(f, definitions.get(f.path))
                if f.row_id:
                    array, _, child = f.path.partition("[].")
                    row = rows_by_array[f"{array}[]"].setdefault(
                        f.row_id, {"row_id": f.row_id, "cells": {}}
                    )
                    row["cells"][child] = view
                else:
                    r.fields[f.path] = view
            r.tables = {k: list(v.values()) for k, v in rows_by_array.items()}
        return results

    async def processing_result(
        self, principal: Principal, document_id: uuid.UUID
    ) -> dict[str, Any]:
        document = await self._document(principal, document_id)
        job = await self._job_with_parts(document)
        latest = await self._session.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.document_id == document.id)
            .order_by(ProcessingJob.created_at.desc())
            .limit(1)
        )
        pages = (
            await self._session.scalars(
                select(DocumentPage)
                .where(DocumentPage.document_id == document.id)
                .order_by(DocumentPage.page_number)
            )
        ).all()
        parts = await self.part_results(job.id) if job else []
        steps = (
            (
                await self._session.scalars(
                    select(ProcessingStep).where(ProcessingStep.job_id == job.id)
                )
            ).all()
            if job
            else []
        )
        cost = sum(float(p.result.cost_estimate) for p in parts if p.result)
        errors = []
        if latest and latest.last_error_code and latest.status.value != "SUCCEEDED":
            errors.append(
                {
                    "category": latest.last_error_category,
                    "code": latest.last_error_code,
                    "message": latest.last_error_message,
                }
            )
        return {
            "document_id": document.id,
            "status": document.status,
            "job_id": job.id if job else None,
            "parts": [
                {
                    "part_id": p.part.id,
                    "pages": [p.part.page_start, p.part.page_end],
                    "classification": {
                        "document_type": p.doc_type.key if p.doc_type else None,
                        "document_type_name": p.doc_type.name if p.doc_type else None,
                        "confidence": p.part.classification_confidence,
                        "classifier": p.part.classifier,
                        "reasons": p.part.classification_reasons,
                    },
                    "schema_version": p.version.version if p.version else None,
                    "extraction": {
                        "route": p.result.route,
                        "providers": p.result.providers,
                        "route_trace": p.result.route_trace,
                        "metrics": p.result.metrics,
                    }
                    if p.result
                    else None,
                    "fields": p.fields,
                    "tables": p.tables,
                    **p.sections,
                }
                for p in parts
            ],
            "pages": [
                {
                    "number": pg.page_number,
                    "text_source": pg.text_source,
                    "text_quality": pg.text_quality,
                    "ocr_status": pg.ocr_status,
                }
                for pg in pages
            ],
            "metrics": {
                "steps": {s.step_key: s.duration_ms for s in steps if s.duration_ms is not None},
                "duration_ms": sum(s.duration_ms or 0 for s in steps),
                "estimated_cost": round(cost, 6),
            },
            "errors": errors,
        }
