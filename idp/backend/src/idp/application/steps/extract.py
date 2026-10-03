"""`extract` step: route and run extraction for every classified part.

The routing engine (domain/routing.py) turns measured signals and the tenant's
processing policy into ordered provider stages. Stage 0 always runs; the next
stage runs only while required fields are missing or below their threshold.
Provider failures are isolated (recorded, counted by the circuit breaker) and
fall through to the next stage; only when *every* attempted provider failed is
the step attempt failed, so the job retries. Results are stored per (job, part)
with full provenance and the route trace.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from idp.application.policy import PolicyResolver
from idp.application.workflows import StepContext, StepResult
from idp.domain.errors import ProviderError
from idp.domain.geometry import PageLayout
from idp.domain.routing import (
    DEFAULT_ROUTING_POLICY,
    Candidate,
    DocumentSignals,
    FieldConfidence,
    PageSignal,
    RoutingPolicy,
    unresolved,
)
from idp.domain.taxonomy import FieldType, SchemaDefinition
from idp.infrastructure.db.models import (
    DocumentPage,
    DocumentPart,
    DocumentType,
    ExtractedField,
    ExtractionResult,
    SchemaVersion,
)
from idp.infrastructure.layout_store import load_layout
from idp.infrastructure.logging import get_logger
from idp.infrastructure.storage.base import ObjectStorageProvider
from idp.providers.extraction.base import ExtractionContext, ExtractionProvider, FieldCandidate
from idp.providers.extraction.merge import MergedField, merge
from idp.providers.resilience import BreakerRegistry

log = get_logger(__name__)


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


def _signals(
    pages: Sequence[DocumentPage],
    part: DocumentPart,
    doc_type: str | None,
    schema: SchemaDefinition,
) -> DocumentSignals:
    return DocumentSignals(
        pages=tuple(
            PageSignal(
                page_number=p.page_number,
                text_source=p.text_source or "none",
                text_quality=p.text_quality,
                ocr_confidence=p.ocr_confidence,
                table_density=p.table_density,
            )
            for p in pages
            if part.page_start <= p.page_number <= part.page_end
        ),
        document_type=doc_type,
        classification_confidence=part.classification_confidence,
        expects_tables=any(f.type is FieldType.ARRAY for _, f in schema.flatten()),
    )


def _open_fields(schema: SchemaDefinition, merged: list[MergedField]) -> list[str]:
    """Required scalars missing/below threshold, and required groups without rows."""
    by_path = {m.path: m for m in merged if not m.row_id}
    states: list[FieldConfidence] = []
    for path, definition in schema.flatten():
        if definition.type is FieldType.ARRAY:
            has_rows = any(m.row_id and m.path.startswith(f"{path}.") for m in merged)
            states.append(
                FieldConfidence(path, definition.required, 1.0 if has_rows else None, 0.0)
            )
        elif "[]" not in path and definition.type is not FieldType.OBJECT:
            item = by_path.get(path)
            confidence = item.best.confidence if item and item.best else None
            states.append(
                FieldConfidence(
                    path, definition.required, confidence, definition.confidence_threshold
                )
            )
    return unresolved(states)


class ExtractStep:
    key = "extract"

    def __init__(
        self,
        *,
        storage: ObjectStorageProvider,
        providers: Sequence[ExtractionProvider],
        policy: PolicyResolver,
        routing: RoutingPolicy = DEFAULT_ROUTING_POLICY,
        breakers: BreakerRegistry | None = None,
        provider_timeout_seconds: float = 120.0,
    ) -> None:
        names = [p.info.name for p in providers]
        if len(names) != len(set(names)):
            raise ValueError("extraction provider names must be unique")
        self._storage = storage
        self._providers = {p.info.name: p for p in providers}
        self._policy = policy
        self._routing = routing
        self._breakers = breakers or BreakerRegistry()
        self._timeout = provider_timeout_seconds

    async def _layouts(self, pages: Sequence[DocumentPage]) -> dict[int, PageLayout]:
        loaded = await asyncio.gather(
            *(load_layout(self._storage, p.layout_key) for p in pages if p.layout_key)
        )
        return {layout.page_number: layout for layout in loaded}

    def _candidates(self) -> list[Candidate]:
        return [
            Candidate(
                name=p.info.name,
                tier=p.info.tier,
                locality=p.info.locality,
                is_mock=p.info.is_mock,
                circuit_open=self._breakers.is_open(p.info.name),
                cost_per_page=p.info.cost_per_page,
                needs_tables=p.info.needs_tables,
            )
            for p in self._providers.values()
        ]

    async def _attempt(
        self, provider: ExtractionProvider, context: ExtractionContext
    ) -> tuple[list[FieldCandidate], dict[str, Any]]:
        name = provider.info.name
        suitability = provider.assess(context)
        if not suitability.can_handle:
            return [], {"provider": name, "status": "skipped", "reasons": list(suitability.reasons)}
        breaker = self._breakers.get(name)
        if not breaker.allow():
            return [], {"provider": name, "status": "circuit_open"}
        started = time.monotonic()
        try:
            found = await asyncio.wait_for(provider.extract(context), self._timeout)
        except Exception as exc:
            breaker.record_failure()
            log.warning("extract.provider_failed", provider=name, error=type(exc).__name__)
            return [], {
                "provider": name,
                "status": "timeout" if isinstance(exc, TimeoutError) else "failed",
                "error": type(exc).__name__,
                "duration_ms": round((time.monotonic() - started) * 1000),
            }
        breaker.record_success()
        cost = round(provider.info.cost_per_page * len(context.layouts), 6)
        return found, {
            "provider": name,
            "version": provider.info.version,
            "status": "ok",
            "candidates": len(found),
            "duration_ms": round((time.monotonic() - started) * 1000),
            "estimated_cost": cost,
        }

    async def run(self, ctx: StepContext) -> StepResult:
        parts = (
            await ctx.session.scalars(
                select(DocumentPart)
                .where(DocumentPart.job_id == ctx.job.id)
                .order_by(DocumentPart.part_index)
            )
        ).all()
        pages = (
            await ctx.session.scalars(
                select(DocumentPage).where(DocumentPage.document_id == ctx.document.id)
            )
        ).all()
        layouts = await self._layouts(pages)
        policy = await self._policy.resolve(ctx.session, ctx.document.tenant_id)
        await ctx.session.execute(
            delete(ExtractionResult).where(ExtractionResult.job_id == ctx.job.id)
        )
        totals = {"parts_extracted": 0, "fields": 0, "missing": 0, "rows": 0}
        confidences: list[float] = []
        routes: list[str] = []
        attempted = failed = 0
        total_cost = 0.0
        for part in parts:
            if part.schema_version_id is None:
                continue  # unclassified: nothing to extract against; review handles it
            version = await ctx.session.get(SchemaVersion, part.schema_version_id)
            if version is None:
                continue
            doc_type = (
                await ctx.session.get(DocumentType, part.document_type_id)
                if part.document_type_id
                else None
            )
            schema = SchemaDefinition.model_validate(version.definition)
            context = ExtractionContext(
                schema=schema,
                layouts=tuple(
                    layouts[n] for n in range(part.page_start, part.page_end + 1) if n in layouts
                ),
            )
            plan = self._routing.plan(
                _signals(pages, part, doc_type.key if doc_type else None, schema),
                self._candidates(),
                policy,
            )
            candidates, used, stages, cost = await self._run_stages(plan.stages, context, schema)
            attempted += sum(1 for s in stages for a in s["attempts"] if a["status"] != "skipped")
            failed += sum(
                1 for s in stages for a in s["attempts"] if a["status"] in {"failed", "timeout"}
            )
            merged = merge(schema, candidates)
            trace = {**plan.trace(), "stages": stages, "estimated_cost": cost}
            result = ExtractionResult(
                tenant_id=ctx.document.tenant_id,
                document_id=ctx.document.id,
                job_id=ctx.job.id,
                part_id=part.id,
                schema_version_id=version.id,
                route=plan.route.value,
                providers=used,
                route_trace=trace,
                cost_estimate=cost,
                metrics={},
            )
            ctx.session.add(result)
            await ctx.session.flush()
            stats = self._persist(ctx, result, part, merged, str(version.id))
            result.metrics = stats
            routes.append(plan.route.value)
            total_cost += cost
            totals["parts_extracted"] += 1
            totals["fields"] += stats["fields"]
            totals["missing"] += stats["missing"]
            totals["rows"] += stats["rows"]
            confidences.extend(m.best.confidence for m in merged if m.best)
        if attempted and failed == attempted:
            # Every provider that ran failed: likely an outage, so retry the step
            # attempt rather than send an empty result to review.
            raise ProviderError("All extraction providers failed", details={"attempted": attempted})
        return StepResult(
            provider="router",
            provider_version=str(self._routing.version),
            metrics={
                **totals,
                "routes": routes,
                "mean_confidence": round(sum(confidences) / len(confidences), 3)
                if confidences
                else None,
                "policy_mode": policy.mode.value,
                "estimated_cost": round(total_cost, 6),
            },
        )

    async def _run_stages(
        self,
        stages: Sequence[Sequence[str]],
        context: ExtractionContext,
        schema: SchemaDefinition,
    ) -> tuple[list[FieldCandidate], list[str], list[dict[str, Any]], float]:
        candidates: list[FieldCandidate] = []
        used: list[str] = []
        trace: list[dict[str, Any]] = []
        cost = 0.0
        for index, stage in enumerate(stages):
            results = await asyncio.gather(
                *(self._attempt(self._providers[name], context) for name in stage)
            )
            attempts = []
            for found, attempt in results:
                candidates.extend(found)
                attempts.append(attempt)
                if attempt["status"] == "ok":
                    used.append(attempt["provider"])
                    cost += attempt["estimated_cost"]
            open_paths = _open_fields(schema, merge(schema, candidates))
            if not open_paths:
                outcome = "accepted"
            elif index + 1 < len(stages):
                outcome = "escalated"
            else:
                outcome = "exhausted"
            trace.append(
                {"stage": index, "attempts": attempts, "unresolved": open_paths, "outcome": outcome}
            )
            if outcome == "accepted":
                break
        return candidates, used, trace, round(cost, 6)

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
