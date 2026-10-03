from itertools import pairwise

import pytest

from idp.domain.errors import ErrorCategory, InvalidStateTransitionError
from idp.domain.lifecycle import (
    ALLOWED_TRANSITIONS,
    REPROCESSABLE,
    DocumentStatus,
    can_transition,
    is_human_intervention,
    transition,
)

S = DocumentStatus


@pytest.mark.parametrize(
    "path",
    [
        # Straight-through processing.
        [S.RECEIVED, S.QUEUED, S.PROCESSING, S.COMPLETED],
        # With business actions.
        [S.RECEIVED, S.QUEUED, S.PROCESSING, S.READY_FOR_ACTION, S.PROCESSING, S.COMPLETED],
        # Human review is a normal detour, not a failure.
        [S.QUEUED, S.PROCESSING, S.WAITING_FOR_HUMAN, S.PROCESSING, S.COMPLETED],
        # Reviewer sends back for reprocessing.
        [S.PROCESSING, S.WAITING_FOR_HUMAN, S.QUEUED, S.PROCESSING],
        # Failure and manual replay.
        [S.PROCESSING, S.FAILED, S.QUEUED, S.PROCESSING],
    ],
)
def test_supported_paths(path: list[DocumentStatus]) -> None:
    for current, target in pairwise(path):
        record = transition(current, target, reason="test")
        assert (record.from_status, record.to_status) == (current, target)


def test_human_review_is_not_a_failure_state() -> None:
    assert is_human_intervention(S.WAITING_FOR_HUMAN)
    assert not can_transition(S.WAITING_FOR_HUMAN, S.FAILED)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.RECEIVED, S.COMPLETED),
        (S.QUEUED, S.COMPLETED),
        (S.COMPLETED, S.PROCESSING),
        (S.REJECTED, S.COMPLETED),
        (S.WAITING_FOR_HUMAN, S.COMPLETED),
        (S.FAILED, S.PROCESSING),
    ],
)
def test_illegal_transitions_raise_business_error(
    current: DocumentStatus, target: DocumentStatus
) -> None:
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        transition(current, target)
    assert exc_info.value.category is ErrorCategory.BUSINESS_ERROR
    assert exc_info.value.details == {"from": current.value, "to": target.value}


def test_every_state_has_an_outgoing_transition_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(DocumentStatus)


@pytest.mark.parametrize("status", sorted(REPROCESSABLE))
def test_reprocessable_states_can_requeue(status: DocumentStatus) -> None:
    assert can_transition(status, S.QUEUED)
