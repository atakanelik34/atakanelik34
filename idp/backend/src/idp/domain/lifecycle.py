"""Document lifecycle and processing-job state machines.

Pure domain logic: no I/O. The application layer persists the returned
`StatusTransition` (status column + audit log) in the same transaction.
See ARCHITECTURE.md §4.

The document status is deliberately coarse and independent of which steps a
workflow contains: per-step progress lives on processing jobs and steps, so
configurable workflows (skip classification, add custom steps) never require a
new document status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from idp.domain.errors import InvalidStateTransitionError


class DocumentStatus(StrEnum):
    RECEIVED = "RECEIVED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    READY_FOR_ACTION = "READY_FOR_ACTION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


S = DocumentStatus

# Statuses a document can be reprocessed from (always as a *new* job/run).
REPROCESSABLE: frozenset[DocumentStatus] = frozenset(
    {S.COMPLETED, S.FAILED, S.REJECTED, S.WAITING_FOR_HUMAN}
)

ALLOWED_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    # RECEIVED -> REJECTED: refused by an ingestion policy (e.g. malware verdict).
    S.RECEIVED: frozenset({S.QUEUED, S.REJECTED}),
    S.QUEUED: frozenset({S.PROCESSING}),
    # Retries keep the document PROCESSING; the job carries the retry state.
    S.PROCESSING: frozenset({S.WAITING_FOR_HUMAN, S.READY_FOR_ACTION, S.COMPLETED, S.FAILED}),
    # Review resumes the same job (-> PROCESSING), sends back (-> QUEUED) or rejects.
    S.WAITING_FOR_HUMAN: frozenset({S.PROCESSING, S.QUEUED, S.REJECTED}),
    S.READY_FOR_ACTION: frozenset({S.PROCESSING, S.COMPLETED}),
    S.COMPLETED: frozenset({S.QUEUED}),
    S.FAILED: frozenset({S.QUEUED}),
    S.REJECTED: frozenset({S.QUEUED}),
}


@dataclass(frozen=True, slots=True)
class StatusTransition:
    """An auditable record of a single lifecycle move."""

    from_status: DocumentStatus
    to_status: DocumentStatus
    reason: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def can_transition(current: DocumentStatus, target: DocumentStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def transition(
    current: DocumentStatus, target: DocumentStatus, *, reason: str | None = None
) -> StatusTransition:
    """Validate a lifecycle move and return its audit record.

    Raises `InvalidStateTransitionError` (a BUSINESS_ERROR) for illegal moves.
    """
    if not can_transition(current, target):
        raise InvalidStateTransitionError(
            f"Illegal document status transition {current} -> {target}",
            details={"from": current.value, "to": target.value},
        )
    return StatusTransition(from_status=current, to_status=target, reason=reason)


def is_human_intervention(status: DocumentStatus) -> bool:
    """Waiting for a human is a normal lifecycle state, not a failure."""
    return status is S.WAITING_FOR_HUMAN


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    SUCCEEDED = "SUCCEEDED"
    # Non-retryable error (e.g. corrupted document): retrying cannot help.
    FAILED = "FAILED"
    # Retryable error, but attempts exhausted: needs operator attention / replay.
    DEAD_LETTERED = "DEAD_LETTERED"


ACTIVE_JOB_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRY_SCHEDULED}
)


class StepStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
