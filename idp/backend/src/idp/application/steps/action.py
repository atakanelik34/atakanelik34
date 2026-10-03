"""`action` step: run the business actions configured for each document type.

Only actions declared in the part's schema (`SchemaDefinition.actions`) can
run, against connections the tenant configured — there is no other way to
cause an external side effect. Each (job, part, action) is one `ActionRun`
with a deterministic idempotency key.

* Actions needing approval (the default) pause the job: document
  READY_FOR_ACTION until someone with `actions:execute` approves or rejects
  them; the job then resumes here and executes the approved ones.
* Execution records its outcome durably *per action*: the step commits each
  action's result in its own transaction while holding the run's row lock, an
  intentional exception to "steps never commit" — an external side effect must
  never be forgotten because a later action failed. A retried or concurrent
  attempt finds the run already succeeded and skips it; receivers get the same
  `Idempotency-Key` should a crash ever leave an outcome unrecorded.
* Transient failures retry the job; permanent refusals fail it (replayable);
  a missing connection or disabled mock fails it with a configuration error.
"""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from idp.application.audit import ActorType, AuditAction, AuditEntity, record_audit
from idp.application.outbox import emit
from idp.application.policy import PolicyResolver
from idp.application.workflows import AwaitingHumanReview, StepContext, StepResult
from idp.domain.actions import ActionContext, build_payload
from idp.domain.errors import ActionFailedError, ConfigurationError, ProviderError
from idp.domain.lifecycle import DocumentStatus
from idp.domain.routing import PolicySnapshot
from idp.domain.taxonomy import ActionSpec, SchemaDefinition
from idp.infrastructure.db.models import (
    ActionRun,
    Connection,
    DocumentPart,
    DocumentType,
    EnrichmentResult,
    ExtractedField,
    ExtractionResult,
    SchemaVersion,
)
from idp.infrastructure.logging import get_logger
from idp.providers.actions.base import ActionError, ActionProvider, ActionRequest
from idp.providers.enrichment.base import ACTION_KINDS

log = get_logger(__name__)

DONE = frozenset({"succeeded", "rejected"})


class ActionStep:
    key = "action"

    def __init__(
        self,
        *,
        providers: Mapping[str, ActionProvider],
        policy: PolicyResolver,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._providers = dict(providers)
        self._policy = policy
        self._timeout = timeout_seconds

    async def _context(self, ctx: StepContext, part: DocumentPart) -> ActionContext:
        doc_type = (
            await ctx.session.get(DocumentType, part.document_type_id)
            if part.document_type_id
            else None
        )
        result = await ctx.session.scalar(
            select(ExtractionResult).where(
                ExtractionResult.part_id == part.id, ExtractionResult.job_id == ctx.job.id
            )
        )
        fields: dict[str, Any] = {}
        rows: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        rejected_rows: set[tuple[str, str]] = set()
        if result is not None:
            for f in (
                await ctx.session.scalars(
                    select(ExtractedField)
                    .where(ExtractedField.result_id == result.id)
                    .order_by(ExtractedField.created_at)
                )
            ).all():
                value = None if f.status in ("missing", "rejected") else f.value
                if not f.row_id:
                    fields[f.path] = value
                    continue
                array, _, child = f.path.partition("[].")
                rows[f"{array}[]"].setdefault(f.row_id, {})[child] = value
                if f.status == "rejected":
                    rejected_rows.add((f"{array}[]", f.row_id))
        tables = {
            array: [
                cells for row_id, cells in by_row.items() if (array, row_id) not in rejected_rows
            ]
            for array, by_row in rows.items()
        }
        enrichment = {
            e.name: e.outputs
            for e in (
                await ctx.session.scalars(
                    select(EnrichmentResult).where(
                        EnrichmentResult.part_id == part.id,
                        EnrichmentResult.job_id == ctx.job.id,
                        EnrichmentResult.status == "matched",
                    )
                )
            ).all()
        }
        return ActionContext(
            document={
                "id": str(ctx.document.id),
                "filename": ctx.document.original_filename,
                "type": doc_type.key if doc_type else None,
                "pages": [part.page_start, part.page_end],
            },
            fields=fields,
            tables=tables,
            enrichment=enrichment,
        )

    async def _plan(self, ctx: StepContext) -> list[ActionRun]:
        """Create (once) the runs this job's parts call for; return all of them."""
        existing = {
            r.idempotency_key: r
            for r in (
                await ctx.session.scalars(select(ActionRun).where(ActionRun.job_id == ctx.job.id))
            ).all()
        }
        parts = (
            await ctx.session.scalars(
                select(DocumentPart)
                .where(DocumentPart.job_id == ctx.job.id)
                .order_by(DocumentPart.part_index)
            )
        ).all()
        runs: list[ActionRun] = []
        for part in parts:
            if part.schema_version_id is None:
                continue
            version = await ctx.session.get(SchemaVersion, part.schema_version_id)
            if version is None:
                continue
            specs: list[ActionSpec] = SchemaDefinition.model_validate(version.definition).actions
            if not specs:
                continue
            action_ctx = await self._context(ctx, part)
            for spec in specs:
                key = f"{ctx.job.id}:{part.id}:{spec.name}"
                run = existing.get(key)
                if run is None:
                    connection = await self._connection(ctx, spec.connection)
                    run = ActionRun(
                        tenant_id=ctx.document.tenant_id,
                        document_id=ctx.document.id,
                        job_id=ctx.job.id,
                        part_id=part.id,
                        name=spec.name,
                        connection_key=spec.connection,
                        kind=connection.kind if connection else "unknown",
                        status="pending_approval" if spec.requires_approval else "approved",
                        idempotency_key=key,
                        requires_approval=spec.requires_approval,
                        attempts=0,
                        payload=build_payload(spec, action_ctx),
                        response={},
                        is_mock=bool(connection and connection.kind == "mock_erp"),
                    )
                    ctx.session.add(run)
                runs.append(run)
        await ctx.session.flush()
        return runs

    async def _connection(self, ctx: StepContext, key: str) -> Connection | None:
        return await ctx.session.scalar(
            select(Connection).where(
                Connection.tenant_id == ctx.document.tenant_id,
                Connection.key == key,
                Connection.is_active.is_(True),
            )
        )

    async def _pause_if_pending(self, ctx: StepContext, runs: list[ActionRun]) -> None:
        pending = [r for r in runs if r.status == "pending_approval"]
        if not pending:
            return
        record_audit(
            ctx.session,
            action=AuditAction.ACTIONS_REQUESTED,
            entity_type=AuditEntity.DOCUMENT,
            entity_id=ctx.document.id,
            tenant_id=ctx.document.tenant_id,
            actor_type=ActorType.SYSTEM,
            after={"actions": [r.name for r in pending]},
        )
        raise AwaitingHumanReview(
            [{"action": r.name, "connection": r.connection_key} for r in pending],
            document_status=DocumentStatus.READY_FOR_ACTION,
            reason="actions awaiting approval",
        )

    async def run(self, ctx: StepContext) -> StepResult:
        runs = await self._plan(ctx)
        if not runs:
            return StepResult(provider="actions", provider_version="1", metrics={"actions": 0})
        await self._pause_if_pending(ctx, runs)  # defensive: the gate normally did this
        policy = await self._policy.resolve(ctx.session, ctx.document.tenant_id)
        await ctx.session.commit()  # persist the plan before any side effect
        for run in runs:
            if run.status == "approved":
                await self._execute(ctx, run.id, policy)
        statuses = Counter(
            r.status
            for r in (
                await ctx.session.scalars(select(ActionRun).where(ActionRun.job_id == ctx.job.id))
            ).all()
        )
        return StepResult(
            provider="actions", provider_version="1", metrics={"actions": dict(statuses)}
        )

    async def _execute(self, ctx: StepContext, run_id: Any, policy: PolicySnapshot) -> None:
        session = ctx.session
        run = await session.get(ActionRun, run_id, with_for_update=True, populate_existing=True)
        if run is None or run.status != "approved":
            await session.commit()  # someone else executed or decided it
            return
        name, key = run.name, run.connection_key  # rollback expires ORM state
        connection = await self._connection(ctx, key)
        provider = self._providers.get(connection.kind) if connection else None
        problem: str | None = None
        if connection is None or provider is None or connection.kind not in ACTION_KINDS:
            problem = f"Action connection '{key}' is not configured"
        elif provider.is_mock and not policy.allow_mock_providers:
            problem = "Mock action providers are disabled by policy"
        elif not provider.configured():
            problem = f"The {connection.kind} action provider is not configured"
        if problem is not None or connection is None or provider is None:
            await session.rollback()
            raise ConfigurationError(problem or "Action not executable", details={"action": name})
        run.attempts += 1
        run.kind, run.is_mock = connection.kind, provider.is_mock
        request = ActionRequest(
            idempotency_key=run.idempotency_key,
            action=run.name,
            document_id=str(run.document_id),
            payload=run.payload,
        )
        try:
            outcome = await asyncio.wait_for(provider.execute(connection, request), self._timeout)
        except (ActionError, TimeoutError) as exc:
            code = exc.code if isinstance(exc, ActionError) else "timeout"
            transient = isinstance(exc, TimeoutError) or (
                isinstance(exc, ActionError) and exc.transient
            )
            run.error_code = code
            if not transient:
                run.status = "failed"
                self._record(ctx, run, AuditAction.ACTION_FAILED, "action.failed")
            await session.commit()
            log.warning("action.failed", action=run.name, error=code, transient=transient)
            if transient:
                raise ProviderError(
                    "Action target temporarily unavailable", details={"action": run.name}
                ) from exc
            raise ActionFailedError(
                f"Action '{run.name}' was refused by its target", details={"error": code}
            ) from exc
        run.status = "succeeded"
        run.error_code = None
        run.external_reference = outcome.external_reference
        run.response = outcome.response
        run.executed_at = datetime.now(UTC)
        self._record(ctx, run, AuditAction.ACTION_SUCCEEDED, "action.succeeded")
        await session.commit()

    @staticmethod
    def _record(ctx: StepContext, run: ActionRun, action: AuditAction, event: str) -> None:
        details = {
            "action": run.name,
            "connection": run.connection_key,
            "external_reference": run.external_reference,
            "error": run.error_code,
            "is_mock": run.is_mock,
        }
        record_audit(
            ctx.session,
            action=action,
            entity_type=AuditEntity.ACTION_RUN,
            entity_id=run.id,
            tenant_id=run.tenant_id,
            actor_type=ActorType.SYSTEM,
            after=details,
        )
        emit(
            ctx.session,
            tenant_id=run.tenant_id,
            event_type=event,
            aggregate_id=run.document_id,
            payload={"document_id": str(run.document_id), "action_run_id": str(run.id), **details},
        )


class ApproveActionsStep(ActionStep):
    """Gate before `action`: plans the runs and waits for approval when required.

    Approval or rejection (application/actions.py) marks this step SUCCEEDED and
    resumes the job, exactly like review approval; `action` then executes the
    approved runs and skips rejected ones.
    """

    key = "approve_actions"

    async def run(self, ctx: StepContext) -> StepResult:
        runs = await self._plan(ctx)
        await self._pause_if_pending(ctx, runs)
        return StepResult(
            provider="action-gate",
            provider_version="1",
            metrics={"actions": len(runs), "approval": "not_required" if runs else "no_actions"},
        )
