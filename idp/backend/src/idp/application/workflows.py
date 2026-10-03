"""Workflow definitions and the step-handler contract.

Phase 2 defines workflows in code, versioned here; phase 8 moves them to
`WorkflowVersion` rows. A job pins (workflow_key, workflow_version) at creation,
so changing a definition never alters how an existing job runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from idp.domain.errors import ConfigurationError
from idp.domain.lifecycle import DocumentStatus
from idp.infrastructure.db.models import Document, ProcessingJob


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    key: str
    version: int
    steps: tuple[str, ...]
    # Document status once every step has succeeded.
    final_status: DocumentStatus = DocumentStatus.COMPLETED


INGEST_V1 = WorkflowDefinition(key="ingest", version=1, steps=("probe",))
INGEST_V2 = WorkflowDefinition(key="ingest", version=2, steps=("probe", "digitize"))
INGEST_V3 = WorkflowDefinition(key="ingest", version=3, steps=("probe", "digitize", "classify"))

DEFAULT_WORKFLOW = INGEST_V3

# Old versions stay registered: jobs pinned to them must still run unchanged.
_REGISTRY: dict[tuple[str, int], WorkflowDefinition] = {
    (w.key, w.version): w for w in (INGEST_V1, INGEST_V2, INGEST_V3)
}


def get_workflow(key: str, version: int) -> WorkflowDefinition:
    try:
        return _REGISTRY[(key, version)]
    except KeyError as exc:
        raise ConfigurationError(f"Unknown workflow {key} v{version}") from exc


@dataclass(slots=True)
class StepContext:
    """What a step may touch. Steps write results through `session` but never commit."""

    session: AsyncSession
    job: ProcessingJob
    document: Document


@dataclass(frozen=True, slots=True)
class StepResult:
    provider: str | None = None
    provider_version: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


class StepHandler(Protocol):
    key: str

    async def run(self, ctx: StepContext) -> StepResult:
        """Do the step's work.

        Contract: perform slow I/O first, then write results through
        `ctx.session` (the runner commits them together with the step record,
        after re-checking the job's lease). Never commit. Must be safe to re-run:
        a crash between the work and the commit repeats the step once.
        """
        ...
