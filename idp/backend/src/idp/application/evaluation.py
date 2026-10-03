"""Evaluation datasets and runs (ARCHITECTURE.md §15).

Ground truth comes from approved reviews: the reviewed values of a part, with
rejected fields as "no value" and deleted rows dropped. A run compares the
*latest machine output* for each item (the `original_value`s of the newest job
that extracted that page range) against the ground truth. To evaluate a new
configuration, reprocess the dataset's documents and run again; each run
records the pipeline/workflow/routing versions it actually measured.

Runs are computed in the request (bounded by `MAX_ITEMS`). Moving them to a
worker is deferred until datasets outgrow that bound.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.audit import AuditAction, AuditEntity, record_audit
from idp.domain.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from idp.domain.evaluation import (
    GroundTruth,
    ItemOutcome,
    MachineOutput,
    Prediction,
    aggregate,
    score_item,
)
from idp.domain.identity import Permission, Principal
from idp.domain.lifecycle import StepStatus
from idp.domain.taxonomy import SchemaDefinition
from idp.infrastructure.db.models import (
    DocumentPart,
    DocumentType,
    EvaluationDataset,
    EvaluationItem,
    EvaluationRun,
    ExtractedField,
    ExtractionResult,
    ProcessingJob,
    ProcessingStep,
    ReviewTask,
    SchemaVersion,
)

MAX_ITEMS = 1000
HUMAN_METHOD = "human"


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    dataset: EvaluationDataset
    document_type_key: str | None
    items: int
    last_run: EvaluationRun | None


def _array_of(path: str) -> tuple[str, str] | None:
    if "[]." not in path:
        return None
    array, cell = path.split("[].", 1)
    return f"{array}[]", cell


def ground_truth_from(fields: Sequence[ExtractedField]) -> dict[str, Any]:
    """Reviewed values of one part: rejected → None, fully rejected rows dropped."""
    scalars: dict[str, Any] = {}
    rows: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    rejected_rows: dict[tuple[str, str], bool] = {}
    for f in fields:
        value = None if f.status == "rejected" else f.value
        split = _array_of(f.path)
        if split is None:
            scalars[f.path] = value
            continue
        array, cell = split
        rows[array].setdefault(f.row_id, {})[cell] = value
        key = (array, f.row_id)
        rejected_rows[key] = rejected_rows.get(key, True) and f.status == "rejected"
    tables = {
        array: [cells for row_id, cells in by_row.items() if not rejected_rows[(array, row_id)]]
        for array, by_row in rows.items()
    }
    return {"fields": scalars, "tables": tables}


def machine_output(fields: Sequence[ExtractedField], thresholds: dict[str, float]) -> MachineOutput:
    """What the pipeline produced, before any human correction."""
    scalars: dict[str, Prediction] = {}
    rows: dict[str, dict[str, dict[str, Prediction]]] = defaultdict(dict)
    for f in fields:
        if f.method == HUMAN_METHOD:
            continue  # added by a reviewer: not machine output
        prediction = Prediction(f.original_value, f.confidence, thresholds.get(f.path, 0.0))
        split = _array_of(f.path)
        if split is None:
            scalars[f.path] = prediction
        else:
            rows[split[0]].setdefault(f.row_id, {})[split[1]] = prediction
    return MachineOutput(
        fields=scalars, tables={a: list(by_row.values()) for a, by_row in rows.items()}
    )


class EvaluationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _require(principal: Principal, permission: Permission) -> None:
        if not principal.has(permission):
            raise AuthorizationError(f"Missing permission '{permission.value}'")

    async def _dataset(self, principal: Principal, dataset_id: uuid.UUID) -> EvaluationDataset:
        dataset = await self._session.get(EvaluationDataset, dataset_id)
        if dataset is None or dataset.tenant_id != principal.tenant_id:
            raise NotFoundError("Evaluation dataset not found")
        return dataset

    async def _type_key(self, type_id: uuid.UUID | None) -> str | None:
        if type_id is None:
            return None
        doc_type = await self._session.get(DocumentType, type_id)
        return doc_type.key if doc_type else None

    # --- datasets -------------------------------------------------------------------

    async def list_datasets(self, principal: Principal) -> list[DatasetSummary]:
        self._require(principal, Permission.CONFIG_READ)
        datasets = (
            await self._session.scalars(
                select(EvaluationDataset)
                .where(EvaluationDataset.tenant_id == principal.tenant_id)
                .order_by(EvaluationDataset.created_at.desc())
            )
        ).all()
        return [await self._summary(d) for d in datasets]

    async def _summary(self, dataset: EvaluationDataset) -> DatasetSummary:
        count = await self._session.scalar(
            select(func.count()).where(EvaluationItem.dataset_id == dataset.id)
        )
        last = await self._session.scalar(
            select(EvaluationRun)
            .where(EvaluationRun.dataset_id == dataset.id)
            .order_by(EvaluationRun.created_at.desc())
            .limit(1)
        )
        return DatasetSummary(
            dataset, await self._type_key(dataset.document_type_id), count or 0, last
        )

    async def get_dataset(self, principal: Principal, dataset_id: uuid.UUID) -> DatasetSummary:
        self._require(principal, Permission.CONFIG_READ)
        return await self._summary(await self._dataset(principal, dataset_id))

    async def create_dataset(
        self,
        principal: Principal,
        *,
        name: str,
        description: str,
        document_type_key: str | None,
    ) -> DatasetSummary:
        self._require(principal, Permission.CONFIG_WRITE)
        type_id = None
        if document_type_key:
            type_id = await self._session.scalar(
                select(DocumentType.id).where(
                    DocumentType.tenant_id == principal.tenant_id,
                    DocumentType.key == document_type_key,
                )
            )
            if type_id is None:
                raise ValidationError(f"Unknown document type '{document_type_key}'")
        dataset = EvaluationDataset(
            tenant_id=principal.tenant_id,
            name=name,
            description=description,
            document_type_id=type_id,
            created_by_id=principal.user_id,
        )
        self._session.add(dataset)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(f"A dataset named '{name}' already exists") from exc
        record_audit(
            self._session,
            action=AuditAction.EVALUATION_DATASET_CREATED,
            entity_type=AuditEntity.EVALUATION_DATASET,
            entity_id=dataset.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            after={"name": name, "document_type": document_type_key},
        )
        await self._session.commit()
        return await self._summary(dataset)

    async def import_reviews(
        self, principal: Principal, dataset_id: uuid.UUID, *, limit: int = 200
    ) -> int:
        """Add the approved reviews not yet in the dataset as ground-truth items."""
        self._require(principal, Permission.CONFIG_WRITE)
        dataset = await self._dataset(principal, dataset_id)
        existing = await self._session.scalar(
            select(func.count()).where(EvaluationItem.dataset_id == dataset.id)
        )
        room = max(0, min(limit, MAX_ITEMS - (existing or 0)))
        known: set[tuple[uuid.UUID, int]] = {
            (document_id, page_start)
            for document_id, page_start in await self._session.execute(
                select(EvaluationItem.document_id, EvaluationItem.page_start).where(
                    EvaluationItem.dataset_id == dataset.id
                )
            )
        }
        query = (
            select(ReviewTask, DocumentPart, DocumentType, ExtractionResult)
            .join(DocumentPart, DocumentPart.job_id == ReviewTask.job_id)
            .join(DocumentType, DocumentType.id == DocumentPart.document_type_id)
            .join(ExtractionResult, ExtractionResult.part_id == DocumentPart.id)
            .where(ReviewTask.tenant_id == principal.tenant_id, ReviewTask.status == "approved")
            .order_by(ReviewTask.resolved_at.desc())
        )
        if dataset.document_type_id:
            query = query.where(DocumentPart.document_type_id == dataset.document_type_id)
        added = 0
        for task, part, doc_type, result in (await self._session.execute(query)).all():
            if added >= room:
                break
            if (part.document_id, part.page_start) in known:
                continue  # newest approved review of a page range wins
            known.add((part.document_id, part.page_start))
            fields = (
                await self._session.scalars(
                    select(ExtractedField).where(ExtractedField.result_id == result.id)
                )
            ).all()
            self._session.add(
                EvaluationItem(
                    tenant_id=principal.tenant_id,
                    dataset_id=dataset.id,
                    document_id=part.document_id,
                    page_start=part.page_start,
                    page_end=part.page_end,
                    document_type_key=doc_type.key,
                    ground_truth=ground_truth_from(fields),
                    source="review",
                    source_review_task_id=task.id,
                )
            )
            added += 1
        if added:
            record_audit(
                self._session,
                action=AuditAction.EVALUATION_ITEMS_IMPORTED,
                entity_type=AuditEntity.EVALUATION_DATASET,
                entity_id=dataset.id,
                tenant_id=principal.tenant_id,
                actor=principal,
                after={"added": added, "source": "approved_reviews"},
            )
        await self._session.commit()
        return added

    async def delete_dataset(self, principal: Principal, dataset_id: uuid.UUID) -> None:
        self._require(principal, Permission.CONFIG_WRITE)
        dataset = await self._dataset(principal, dataset_id)
        record_audit(
            self._session,
            action=AuditAction.EVALUATION_DATASET_DELETED,
            entity_type=AuditEntity.EVALUATION_DATASET,
            entity_id=dataset.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            before={"name": dataset.name},
        )
        await self._session.delete(dataset)
        await self._session.commit()

    # --- runs -------------------------------------------------------------------------

    async def list_runs(self, principal: Principal, dataset_id: uuid.UUID) -> list[EvaluationRun]:
        self._require(principal, Permission.CONFIG_READ)
        dataset = await self._dataset(principal, dataset_id)
        return list(
            (
                await self._session.scalars(
                    select(EvaluationRun)
                    .where(EvaluationRun.dataset_id == dataset.id)
                    .order_by(EvaluationRun.created_at.desc())
                    .limit(50)
                )
            ).all()
        )

    async def get_run(self, principal: Principal, run_id: uuid.UUID) -> EvaluationRun:
        self._require(principal, Permission.CONFIG_READ)
        run = await self._session.get(EvaluationRun, run_id)
        if run is None or run.tenant_id != principal.tenant_id:
            raise NotFoundError("Evaluation run not found")
        return run

    async def _latest_result(self, item: EvaluationItem) -> ExtractionResult | None:
        return await self._session.scalar(
            select(ExtractionResult)
            .join(DocumentPart, DocumentPart.id == ExtractionResult.part_id)
            .where(
                ExtractionResult.document_id == item.document_id,
                DocumentPart.page_start == item.page_start,
                DocumentPart.page_end == item.page_end,
            )
            .order_by(ExtractionResult.created_at.desc())
            .limit(1)
        )

    async def _latency_ms(self, job_id: uuid.UUID) -> int | None:
        """Machine processing time: successful steps, excluding time waiting for a human."""
        total = await self._session.scalar(
            select(func.sum(ProcessingStep.duration_ms)).where(
                ProcessingStep.job_id == job_id,
                ProcessingStep.step_key != "review",
                ProcessingStep.status == StepStatus.SUCCEEDED,
            )
        )
        return int(total) if total is not None else None

    async def run(self, principal: Principal, dataset_id: uuid.UUID) -> EvaluationRun:
        self._require(principal, Permission.CONFIG_WRITE)
        dataset = await self._dataset(principal, dataset_id)
        items = (
            await self._session.scalars(
                select(EvaluationItem)
                .where(EvaluationItem.dataset_id == dataset.id)
                .order_by(EvaluationItem.created_at)
                .limit(MAX_ITEMS)
            )
        ).all()
        if not items:
            raise ValidationError("The dataset has no items to evaluate")
        outcomes: list[ItemOutcome] = []
        item_results: list[dict[str, Any]] = []
        versions: dict[str, set[str]] = defaultdict(set)
        for item in items:
            result = await self._latest_result(item)
            if result is None:
                item_results.append({"item_id": str(item.id), "status": "no_result"})
                continue
            job = await self._session.get(ProcessingJob, result.job_id)
            fields = (
                await self._session.scalars(
                    select(ExtractedField).where(ExtractedField.result_id == result.id)
                )
            ).all()
            version = await self._session.get(SchemaVersion, result.schema_version_id)
            schema = SchemaDefinition.model_validate(version.definition) if version else None
            thresholds = (
                {path: d.confidence_threshold for path, d in schema.flatten()} if schema else {}
            )
            truth = GroundTruth(
                fields=item.ground_truth.get("fields", {}),
                tables=item.ground_truth.get("tables", {}),
            )
            scores = score_item(truth, machine_output(fields, thresholds))
            reviewed = await self._session.scalar(
                select(func.count()).where(ReviewTask.job_id == result.job_id)
            )
            outcome = ItemOutcome(
                scores=scores,
                needed_review=bool(reviewed),
                cost=float(result.cost_estimate),
                latency_ms=await self._latency_ms(result.job_id),
            )
            outcomes.append(outcome)
            if job is not None:
                versions["pipeline_version"].add(job.pipeline_version)
                versions["workflow"].add(f"{job.workflow_key}@v{job.workflow_version}")
            versions["route"].add(result.route)
            routing_version = result.route_trace.get("routing_version")
            if routing_version is not None:
                versions["routing_version"].add(str(routing_version))
            correct = sum(s.tp for s in scores.values())
            compared = sum(s.compared for s in scores.values())
            item_results.append(
                {
                    "item_id": str(item.id),
                    "document_id": str(item.document_id),
                    "pages": [item.page_start, item.page_end],
                    "job_id": str(result.job_id),
                    "status": "scored",
                    "correct": correct,
                    "compared": compared,
                    "needed_review": outcome.needed_review,
                    "wrong_fields": sorted(p for p, s in scores.items() if s.fp or s.fn),
                }
            )
        metrics, per_field = aggregate(outcomes)
        metrics["items_without_result"] = sum(1 for r in item_results if r["status"] == "no_result")
        run = EvaluationRun(
            tenant_id=principal.tenant_id,
            dataset_id=dataset.id,
            status="completed",
            created_by_id=principal.user_id,
            config={k: sorted(v) for k, v in versions.items()},
            metrics=metrics,
            field_metrics=per_field,
            item_results=item_results,
            finished_at=datetime.now(UTC),
        )
        self._session.add(run)
        await self._session.flush()
        record_audit(
            self._session,
            action=AuditAction.EVALUATION_RUN_COMPLETED,
            entity_type=AuditEntity.EVALUATION_DATASET,
            entity_id=dataset.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            after={"run_id": str(run.id), "items": len(items), "f1": metrics.get("f1")},
        )
        await self._session.commit()
        return run
