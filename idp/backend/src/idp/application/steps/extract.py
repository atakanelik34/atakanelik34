"""`extract` step: run the extraction route for every classified part.

Phase 5 route: regex -> key/value -> table (deterministic, local, free). Phase 8
replaces the fixed route with the routing engine; phase 9 adds LLM providers as
routed fallbacks. Results are stored per (job, part) with full provenance.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from idp.application.workflows import StepContext, StepResult
from idp.domain.geometry import PageLayout
from idp.domain.taxonomy import SchemaDefinition
from idp.infrastructure.db.models import (
    DocumentPage,
    DocumentPart,
    ExtractedField,
    ExtractionResult,
    SchemaVersion,
)
from idp.infrastructure.layout_store import load_layout
from idp.infrastructure.storage.base import ObjectStorageProvider
from idp.providers.extraction.base import ExtractionContext, ExtractionProvider, FieldCandidate
from idp.providers.extraction.merge import MergedField, merge


def provenance(
    c: FieldCandidate, *, schema_version_id: str, pipeline_version: str
) -> dict[str, Any]:
    return {
        "method": c.method,
        "provider": c.provider,
        "provider_version": c.provider_version,
        "page": c.page,
        "bbox": c.bbox.as_list() if c.bbox else None,
        "source_text": c.raw_text,
        "extracted_at": datetime.now(UTC).isoformat(),
        "pipeline_version": pipeline_version,
        "schema_version_id": schema_version_id,
        "normalization_error": c.reason,
    }


class ExtractStep:
    key = "extract"

    def __init__(
        self,
        *,
        storage: ObjectStorageProvider,
        providers: Sequence[ExtractionProvider],
        route_name: str = "DETERMINISTIC",
    ) -> None:
        self._storage = storage
        self._providers = providers
        self._route = route_name

    async def _layouts(self, ctx: StepContext) -> dict[int, PageLayout]:
        pages = (
            await ctx.session.scalars(
                select(DocumentPage).where(DocumentPage.document_id == ctx.document.id)
            )
        ).all()
        loaded = await asyncio.gather(
            *(load_layout(self._storage, p.layout_key) for p in pages if p.layout_key)
        )
        return {layout.page_number: layout for layout in loaded}

    async def run(self, ctx: StepContext) -> StepResult:
        parts = (
            await ctx.session.scalars(
                select(DocumentPart)
                .where(DocumentPart.job_id == ctx.job.id)
                .order_by(DocumentPart.part_index)
            )
        ).all()
        layouts = await self._layouts(ctx)
        await ctx.session.execute(
            delete(ExtractionResult).where(ExtractionResult.job_id == ctx.job.id)
        )
        totals = {"parts_extracted": 0, "fields": 0, "missing": 0, "rows": 0}
        confidences: list[float] = []
        for part in parts:
            if part.schema_version_id is None:
                continue  # unclassified: nothing to extract against; review handles it
            version = await ctx.session.get(SchemaVersion, part.schema_version_id)
            if version is None:
                continue
            schema = SchemaDefinition.model_validate(version.definition)
            context = ExtractionContext(
                schema=schema,
                layouts=tuple(
                    layouts[n] for n in range(part.page_start, part.page_end + 1) if n in layouts
                ),
            )
            candidates: list[FieldCandidate] = []
            used = []
            for provider in self._providers:
                if provider.assess(context).can_handle:
                    candidates.extend(await provider.extract(context))
                    used.append(provider.info.name)
            merged = merge(schema, candidates)
            result = ExtractionResult(
                tenant_id=ctx.document.tenant_id,
                document_id=ctx.document.id,
                job_id=ctx.job.id,
                part_id=part.id,
                schema_version_id=version.id,
                route=self._route,
                providers=used,
                metrics={},
            )
            ctx.session.add(result)
            await ctx.session.flush()
            stats = self._persist(ctx, result, part, merged, str(version.id))
            result.metrics = stats
            totals["parts_extracted"] += 1
            totals["fields"] += stats["fields"]
            totals["missing"] += stats["missing"]
            totals["rows"] += stats["rows"]
            confidences.extend(m.best.confidence for m in merged if m.best)
        return StepResult(
            provider=self._route.lower(),
            provider_version="1",
            metrics={
                **totals,
                "mean_confidence": round(sum(confidences) / len(confidences), 3)
                if confidences
                else None,
                "providers": [p.info.name for p in self._providers],
                "estimated_cost": 0.0,
            },
        )

    def _persist(
        self,
        ctx: StepContext,
        result: ExtractionResult,
        part: DocumentPart,
        merged: list[MergedField],
        schema_version_id: str,
    ) -> dict[str, int]:
        pipeline_version = ctx.job.pipeline_version
        rows: set[str] = set()
        missing = 0
        for item in merged:
            best = item.best
            if best is None:
                missing += 1
            elif item.row_id:
                rows.add(item.row_id)
            ctx.session.add(
                ExtractedField(
                    tenant_id=part.tenant_id,
                    result_id=result.id,
                    part_id=part.id,
                    path=item.path,
                    row_id=item.row_id,
                    value=best.value if best else None,
                    original_value=best.value if best else None,
                    raw_text=best.raw_text if best else None,
                    confidence=best.confidence if best else 0.0,
                    status="extracted" if best else "missing",
                    normalized=best.normalized if best else True,
                    method=best.method if best else None,
                    provider=best.provider if best else None,
                    provenance=provenance(
                        best, schema_version_id=schema_version_id, pipeline_version=pipeline_version
                    )
                    if best
                    else {},
                    alternatives=[
                        {
                            "value": a.value,
                            "confidence": a.confidence,
                            "method": a.method,
                            "page": a.page,
                            "bbox": a.bbox.as_list() if a.bbox else None,
                            "source_text": a.raw_text,
                        }
                        for a in item.alternatives
                    ],
                )
            )
        return {"fields": len(merged) - missing, "missing": missing, "rows": len(rows)}
