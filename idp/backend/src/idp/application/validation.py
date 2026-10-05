"""Validation of a part: schema rules + part-level checks, persisted as current state.

Used by the `validate` step and again whenever a reviewer changes a field, so
the validation shown always reflects the current values.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from idp.domain.geometry import OcrStatus, TextSource
from idp.domain.taxonomy import SchemaDefinition
from idp.domain.validation import FieldState, Issue, Outcome, ValidationContext, evaluate
from idp.infrastructure.db.models import (
    DocumentPage,
    DocumentPart,
    EnrichmentResult,
    ExtractedField,
    ExtractionResult,
    SchemaVersion,
    ValidationResult,
)


def _ocr_issues(part: DocumentPart, pages: Sequence[DocumentPage]) -> list[Issue]:
    """Text read by OCR is always confirmed by a person (phase 13, P0-2).

    Field confidence already carries OCR word confidence, but calibration on scans is
    synthetic only (docs/validation/CALIBRATION.md: scanned amounts were wrong at
    confidence up to 0.84), so a scanned part never completes - or reaches an ERP
    posting - without review. Approval records the rule as overridden.
    """
    scanned = [
        p.page_number
        for p in pages
        if part.page_start <= p.page_number <= part.page_end
        and p.text_source == TextSource.OCR.value
    ]
    if not scanned:
        return []
    return [
        Issue(
            rule_id="part:ocr",
            rule_type="ocr",
            outcome=Outcome.REQUIRES_HUMAN,
            fields=(),
            message=(
                f"Page(s) {', '.join(map(str, scanned))} were read by OCR: "
                "a person must confirm the values"
            ),
            details={"pages": scanned},
        )
    ]


def _page_issues(part: DocumentPart, pages: Sequence[DocumentPage]) -> list[Issue]:
    unreadable = [
        p.page_number
        for p in pages
        if part.page_start <= p.page_number <= part.page_end
        and (p.text_source == TextSource.NONE.value or p.ocr_status == OcrStatus.FAILED.value)
    ]
    if not unreadable:
        return []
    not_configured = any(
        p.ocr_status == OcrStatus.NOT_CONFIGURED.value for p in pages if p.page_number in unreadable
    )
    reason = "OCR is not configured" if not_configured else "no readable text"
    return [
        Issue(
            rule_id="part:readability",
            rule_type="readability",
            outcome=Outcome.REQUIRES_HUMAN,
            fields=(),
            message=f"Page(s) {', '.join(map(str, unreadable))}: {reason}",
            details={"pages": unreadable},
        )
    ]


async def validate_part(
    session: AsyncSession, part: DocumentPart, pages: Sequence[DocumentPage]
) -> list[Issue]:
    issues = _page_issues(part, pages) + _ocr_issues(part, pages)
    if part.schema_version_id is None:
        issues.append(
            Issue(
                rule_id="part:classification",
                rule_type="classification",
                outcome=Outcome.REQUIRES_HUMAN,
                fields=(),
                message="The document type could not be determined",
            )
        )
    else:
        version = await session.get(SchemaVersion, part.schema_version_id)
        result = await session.scalar(
            select(ExtractionResult).where(
                ExtractionResult.part_id == part.id, ExtractionResult.job_id == part.job_id
            )
        )
        if version is not None and result is not None:
            schema = SchemaDefinition.model_validate(version.definition)
            fields = (
                await session.scalars(
                    select(ExtractedField).where(ExtractedField.result_id == result.id)
                )
            ).all()
            ctx = ValidationContext(schema=schema, fields={})
            rows: dict[str, dict[str, dict[str, FieldState]]] = defaultdict(dict)
            for f in fields:
                state = FieldState(value=f.value, confidence=f.confidence, status=f.status)
                if f.row_id:
                    array, _, child = f.path.partition("[].")
                    rows[f"{array}[]"].setdefault(f.row_id, {})[child] = state
                else:
                    ctx.fields[f.path] = state
            ctx.rows = {k: list(v.values()) for k, v in rows.items()}
            enrichment = await session.execute(
                select(EnrichmentResult.name, EnrichmentResult.status).where(
                    EnrichmentResult.part_id == part.id, EnrichmentResult.job_id == part.job_id
                )
            )
            ctx.enrichment = {row.name: row.status for row in enrichment}
            issues.extend(evaluate(ctx))

    await session.execute(
        delete(ValidationResult).where(
            ValidationResult.part_id == part.id, ValidationResult.job_id == part.job_id
        )
    )
    session.add_all(
        ValidationResult(
            tenant_id=part.tenant_id,
            document_id=part.document_id,
            job_id=part.job_id,
            part_id=part.id,
            rule_id=i.rule_id,
            rule_type=i.rule_type,
            outcome=i.outcome.value,
            field_paths=list(i.fields),
            message=i.message,
            details=i.details,
        )
        for i in issues
    )
    return issues
