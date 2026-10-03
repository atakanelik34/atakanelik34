"""`enrich` step: look up extracted entities in configured connections.

Runs after extraction and before validation, so `lookup` rules can check the
outcome. For each enrichment rule of a part's schema, the current field values
become lookup criteria; the provider for the connection's kind returns
candidate records, which are scored locally (domain/matching.py) and decided as
matched / not_found / ambiguous. Unavailable connections, disabled mocks and
provider errors are recorded as such and never fail the document: whether they
matter is decided by validation (`lookup` rules) and, if needed, a human.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete, select

from idp.application.policy import PolicyResolver
from idp.application.workflows import StepContext, StepResult
from idp.domain.matching import Decision, Match, decide
from idp.domain.routing import PolicySnapshot
from idp.domain.taxonomy import EnrichmentRule, SchemaDefinition
from idp.infrastructure.db.models import (
    Connection,
    DocumentPart,
    EnrichmentResult,
    ExtractedField,
    ExtractionResult,
    SchemaVersion,
)
from idp.infrastructure.logging import get_logger
from idp.providers.enrichment.base import EnrichmentError, EnrichmentProvider
from idp.providers.resilience import BreakerRegistry

log = get_logger(__name__)


def _candidate(m: Match) -> dict[str, Any]:
    return {
        "key": m.record.key,
        "name": m.record.name,
        "score": round(m.score, 4),
        "matched_on": list(m.matched_on),
    }


def _outputs(decision: Decision, rule: EnrichmentRule) -> dict[str, Any]:
    if decision.best is None:
        return {}
    record = decision.best.record
    attributes = dict(record.attributes)
    if rule.outputs:
        attributes = {k: v for k, v in attributes.items() if k in rule.outputs}
    return {"key": record.key, "name": record.name, **attributes}


class EnrichStep:
    key = "enrich"

    def __init__(
        self,
        *,
        providers: Mapping[str, EnrichmentProvider],
        policy: PolicyResolver,
        breakers: BreakerRegistry | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._providers = dict(providers)
        self._policy = policy
        self._breakers = breakers or BreakerRegistry()
        self._timeout = timeout_seconds

    async def run(self, ctx: StepContext) -> StepResult:
        await ctx.session.execute(
            delete(EnrichmentResult).where(EnrichmentResult.job_id == ctx.job.id)
        )
        parts = (
            await ctx.session.scalars(
                select(DocumentPart)
                .where(DocumentPart.job_id == ctx.job.id)
                .order_by(DocumentPart.part_index)
            )
        ).all()
        policy = await self._policy.resolve(ctx.session, ctx.document.tenant_id)
        statuses: Counter[str] = Counter()
        for part in parts:
            if part.schema_version_id is None:
                continue
            version = await ctx.session.get(SchemaVersion, part.schema_version_id)
            if version is None:
                continue
            schema = SchemaDefinition.model_validate(version.definition)
            if not schema.enrichment:
                continue
            values = await self._values(ctx, part)
            for rule in schema.enrichment:
                result = await self._enrich(ctx, part, rule, values, policy)
                ctx.session.add(result)
                statuses[result.status] += 1
        return StepResult(
            provider="enrichment",
            provider_version="1",
            metrics={"lookups": sum(statuses.values()), "statuses": dict(statuses)},
        )

    async def _values(self, ctx: StepContext, part: DocumentPart) -> dict[str, Any]:
        result = await ctx.session.scalar(
            select(ExtractionResult).where(
                ExtractionResult.part_id == part.id, ExtractionResult.job_id == ctx.job.id
            )
        )
        if result is None:
            return {}
        fields = (
            await ctx.session.scalars(
                select(ExtractedField).where(
                    ExtractedField.result_id == result.id, ExtractedField.row_id == ""
                )
            )
        ).all()
        return {
            f.path: f.value
            for f in fields
            if f.status not in ("missing", "rejected") and f.value not in (None, "")
        }

    async def _enrich(
        self,
        ctx: StepContext,
        part: DocumentPart,
        rule: EnrichmentRule,
        values: Mapping[str, Any],
        policy: PolicySnapshot,
    ) -> EnrichmentResult:
        criteria = {a: str(values[p]) for a, p in rule.match.items() if p in values}
        result = EnrichmentResult(
            tenant_id=ctx.document.tenant_id,
            document_id=ctx.document.id,
            job_id=ctx.job.id,
            part_id=part.id,
            name=rule.name,
            connection_key=rule.connection,
            provider="",
            status="skipped",
            criteria=criteria,
            matched_on=[],
            outputs={},
            candidates=[],
            is_mock=False,
            message="",
        )
        connection = await ctx.session.scalar(
            select(Connection).where(
                Connection.tenant_id == ctx.document.tenant_id,
                Connection.key == rule.connection,
                Connection.is_active.is_(True),
            )
        )
        provider = self._providers.get(connection.kind) if connection else None
        if connection is None or provider is None:
            result.status, result.message = "not_configured", "connection not configured"
            return result
        result.provider, result.is_mock = provider.kind, provider.is_mock
        if provider.is_mock and not policy.allow_mock_providers:
            result.status, result.message = "not_configured", "mock provider disabled by policy"
            return result
        if not criteria:
            result.message = "no extracted values to look up"
            return result
        breaker = self._breakers.get(f"enrichment:{connection.id}")
        if not breaker.allow():
            result.status, result.message = "error", "circuit open after repeated failures"
            return result
        try:
            matches = await asyncio.wait_for(
                provider.lookup(ctx.session, connection, rule.entity, criteria), self._timeout
            )
        except (EnrichmentError, TimeoutError) as exc:
            breaker.record_failure()
            code = exc.code if isinstance(exc, EnrichmentError) else "timeout"
            log.warning("enrich.lookup_failed", connection=rule.connection, error=code)
            result.status, result.message = "error", code
            return result
        breaker.record_success()
        decision = decide(matches, rule.min_score)
        result.status = decision.status
        result.candidates = [_candidate(m) for m in decision.candidates]
        if decision.best is not None:
            result.record_key = decision.best.record.key
            result.score = round(decision.best.score, 4)
            result.matched_on = list(decision.best.matched_on)
            result.outputs = _outputs(decision, rule)
        return result
