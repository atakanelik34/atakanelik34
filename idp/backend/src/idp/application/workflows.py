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
INGEST_V4 = WorkflowDefinition(
    key="ingest", version=4, steps=("probe", "digitize", "classify", "extract")
)

INGEST_V5 = WorkflowDefinition(
    key="ingest",
    version=5,
    steps=("probe", "digitize", "classify", "extract", "validate", "review"),
)

# Phase 10: enrichment between extraction and validation (lookup rules need it).
INGEST_V6 = WorkflowDefinition(
    key="ingest",
    version=6,
    steps=("probe", "digitize", "classify", "extract", "enrich", "validate", "review"),
)

# Phase 11: configured business actions after review (approval-gated by default).
INGEST_V7 = WorkflowDefinition(
    key="ingest",
    version=7,
    steps=(
        "probe",
        "digitize",
        "classify",
        "extract",
        "enrich",
        "validate",
        "review",
        "approve_actions",
        "action",
    ),
)

DEFAULT_WORKFLOW = INGEST_V7

# Old versions stay registered: jobs pinned to them must still run unchanged.
_REGISTRY: dict[tuple[str, int], WorkflowDefinition] = {
    (w.key, w.version): w
    for w in (INGEST_V1, INGEST_V2, INGEST_V3, INGEST_V4, INGEST_V5, INGEST_V6, INGEST_V7)
}


def list_workflows() -> list[WorkflowDefinition]:
    return sorted(_REGISTRY.values(), key=lambda w: (w.key, w.version))


def get_workflow(key: str, version: int) -> WorkflowDefinition:
    try:
        return _REGISTRY[(key, version)]
    except KeyError as exc:
        raise ConfigurationError(f"Unknown workflow {key} v{version}") from exc


class AwaitingHumanReview(Exception):  # noqa: N818 — a control-flow signal, not an error
    """Raised by a step to pause the job for a human decision.

    The runner persists the step's pending writes (e.g. the review task), marks
    the step WAITING, the job WAITING_FOR_REVIEW and the document
    WAITING_FOR_HUMAN — all fenced, in one transaction.
    """

    def __init__(
        self,
        reasons: list[dict[str, Any]],
        *,
        document_status: DocumentStatus = DocumentStatus.WAITING_FOR_HUMAN,
        reason: str = "human review required",
    ) -> None:
        super().__init__(reason)
        self.reasons = reasons
        # WAITING_FOR_HUMAN (review) or READY_FOR_ACTION (action approval).
        self.document_status = document_status
        self.reason = reason


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
