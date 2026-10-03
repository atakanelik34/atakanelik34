from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from idp.api.deps import SessionDep, require
from idp.application.evaluation import DatasetSummary, EvaluationService
from idp.domain.identity import Permission, Principal
from idp.infrastructure.db.models import EvaluationRun

router = APIRouter(prefix="/evaluation", tags=["evaluation"])

Reader = Annotated[Principal, Depends(require(Permission.CONFIG_READ))]
Writer = Annotated[Principal, Depends(require(Permission.CONFIG_WRITE))]


def get_service(session: SessionDep) -> EvaluationService:
    return EvaluationService(session)


Service = Annotated[EvaluationService, Depends(get_service)]


class RunSummaryOut(BaseModel):
    id: uuid.UUID
    status: str
    created_at: datetime
    finished_at: datetime | None
    metrics: dict[str, Any]
    config: dict[str, Any]


class RunOut(RunSummaryOut):
    dataset_id: uuid.UUID
    field_metrics: dict[str, Any]
    item_results: list[dict[str, Any]]


class DatasetOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    document_type: str | None
    items: int
    created_at: datetime
    last_run: RunSummaryOut | None


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    document_type: str | None = Field(default=None, max_length=64)


class ImportResult(BaseModel):
    added: int


def _run_summary(run: EvaluationRun) -> RunSummaryOut:
    return RunSummaryOut(
        id=run.id,
        status=run.status,
        created_at=run.created_at,
        finished_at=run.finished_at,
        metrics=run.metrics,
        config=run.config,
    )


def _dataset(summary: DatasetSummary) -> DatasetOut:
    d = summary.dataset
    return DatasetOut(
        id=d.id,
        name=d.name,
        description=d.description,
        document_type=summary.document_type_key,
        items=summary.items,
        created_at=d.created_at,
        last_run=_run_summary(summary.last_run) if summary.last_run else None,
    )


@router.get("/datasets", response_model=list[DatasetOut])
async def list_datasets(principal: Reader, service: Service) -> list[DatasetOut]:
    return [_dataset(s) for s in await service.list_datasets(principal)]


@router.post("/datasets", response_model=DatasetOut, status_code=status.HTTP_201_CREATED)
async def create_dataset(body: DatasetCreate, principal: Writer, service: Service) -> DatasetOut:
    return _dataset(
        await service.create_dataset(
            principal,
            name=body.name,
            description=body.description,
            document_type_key=body.document_type,
        )
    )


@router.get("/datasets/{dataset_id}", response_model=DatasetOut)
async def get_dataset(dataset_id: uuid.UUID, principal: Reader, service: Service) -> DatasetOut:
    return _dataset(await service.get_dataset(principal, dataset_id))


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(dataset_id: uuid.UUID, principal: Writer, service: Service) -> None:
    await service.delete_dataset(principal, dataset_id)


@router.post("/datasets/{dataset_id}/import-reviews", response_model=ImportResult)
async def import_reviews(
    dataset_id: uuid.UUID,
    principal: Writer,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> ImportResult:
    return ImportResult(added=await service.import_reviews(principal, dataset_id, limit=limit))


@router.get("/datasets/{dataset_id}/runs", response_model=list[RunSummaryOut])
async def list_runs(
    dataset_id: uuid.UUID, principal: Reader, service: Service
) -> list[RunSummaryOut]:
    return [_run_summary(r) for r in await service.list_runs(principal, dataset_id)]


@router.post(
    "/datasets/{dataset_id}/runs", response_model=RunOut, status_code=status.HTTP_201_CREATED
)
async def start_run(dataset_id: uuid.UUID, principal: Writer, service: Service) -> RunOut:
    return _run(await service.run(principal, dataset_id))


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, principal: Reader, service: Service) -> RunOut:
    return _run(await service.get_run(principal, run_id))


def _run(run: EvaluationRun) -> RunOut:
    return RunOut(
        **_run_summary(run).model_dump(),
        dataset_id=run.dataset_id,
        field_metrics=run.field_metrics,
        item_results=run.item_results,
    )
