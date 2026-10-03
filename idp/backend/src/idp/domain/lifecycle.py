"""Document processing lifecycle state machine.

Pure domain logic: no I/O. The application layer persists the returned
`StatusTransition` (status column + audit log) in the same transaction.
See ARCHITECTURE.md §4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from idp.domain.errors import InvalidStateTransitionError


class DocumentStatus(StrEnum):
    RECEIVED = "RECEIVED"
    QUEUED = "QUEUED"
    DIGITIZING = "DIGITIZING"
    DIGITIZED = "DIGITIZED"
    CLASSIFYING = "CLASSIFYING"
    CLASSIFIED = "CLASSIFIED"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"
    VALIDATING = "VALIDATING"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    VALIDATED = "VALIDATED"
    ENRICHING = "ENRICHING"
    READY_FOR_ACTION = "READY_FOR_ACTION"
    EXECUTING_ACTIONS = "EXECUTING_ACTIONS"
    COMPLETED = "COMPLETED"
    RETRYING = "RETRYING"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


S = DocumentStatus

# States in which a worker is actively doing something; these may fail or retry.
IN_PROGRESS_STATES: frozenset[DocumentStatus] = frozenset(
    {S.DIGITIZING, S.CLASSIFYING, S.EXTRACTING, S.VALIDATING, S.ENRICHING, S.EXECUTING_ACTIONS}
)

TERMINAL_STATES: frozenset[DocumentStatus] = frozenset({S.COMPLETED, S.REJECTED, S.FAILED})

_HAPPY_PATH: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    S.RECEIVED: frozenset({S.QUEUED, S.REJECTED}),
    S.QUEUED: frozenset({S.DIGITIZING}),
    S.DIGITIZING: frozenset({S.DIGITIZED}),
    S.DIGITIZED: frozenset({S.CLASSIFYING}),
    S.CLASSIFYING: frozenset({S.CLASSIFIED}),
    S.CLASSIFIED: frozenset({S.EXTRACTING}),
    S.EXTRACTING: frozenset({S.EXTRACTED}),
    S.EXTRACTED: frozenset({S.VALIDATING}),
    S.VALIDATING: frozenset({S.VALIDATED, S.WAITING_FOR_HUMAN}),
    S.WAITING_FOR_HUMAN: frozenset({S.VALIDATING, S.QUEUED, S.REJECTED}),
    S.VALIDATED: frozenset({S.ENRICHING, S.READY_FOR_ACTION}),
    S.ENRICHING: frozenset({S.READY_FOR_ACTION, S.WAITING_FOR_HUMAN}),
    S.READY_FOR_ACTION: frozenset({S.EXECUTING_ACTIONS, S.COMPLETED}),
    S.EXECUTING_ACTIONS: frozenset({S.COMPLETED}),
    S.RETRYING: frozenset({S.QUEUED, S.FAILED}),
    # Terminal states only leave via an explicit reprocess, which starts a new run.
    S.FAILED: frozenset({S.QUEUED}),
    S.COMPLETED: frozenset({S.QUEUED}),
    S.REJECTED: frozenset({S.QUEUED}),
}


def _build_transitions() -> dict[DocumentStatus, frozenset[DocumentStatus]]:
    table = dict(_HAPPY_PATH)
    for state in IN_PROGRESS_STATES:
        table[state] = table[state] | {S.RETRYING, S.FAILED}
    return table


ALLOWED_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = _build_transitions()


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
