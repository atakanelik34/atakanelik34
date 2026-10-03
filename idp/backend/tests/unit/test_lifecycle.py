from itertools import pairwise

import pytest

from idp.domain.errors import ErrorCategory, InvalidStateTransitionError
from idp.domain.lifecycle import (
    ALLOWED_TRANSITIONS,
    IN_PROGRESS_STATES,
    DocumentStatus,
    can_transition,
    is_human_intervention,
    transition,
)

S = DocumentStatus

HAPPY_PATH = [
    S.RECEIVED,
    S.QUEUED,
    S.DIGITIZING,
    S.DIGITIZED,
    S.CLASSIFYING,
    S.CLASSIFIED,
    S.EXTRACTING,
    S.EXTRACTED,
    S.VALIDATING,
    S.VALIDATED,
    S.ENRICHING,
    S.READY_FOR_ACTION,
    S.EXECUTING_ACTIONS,
    S.COMPLETED,
]


def test_happy_path_is_fully_allowed() -> None:
    for current, target in pairwise(HAPPY_PATH):
        record = transition(current, target, reason="step done")
        assert (record.from_status, record.to_status) == (current, target)


def test_human_review_loop_is_a_normal_path() -> None:
    path = [S.VALIDATING, S.WAITING_FOR_HUMAN, S.VALIDATING, S.VALIDATED]
    for current, target in pairwise(path):
        transition(current, target)
    assert is_human_intervention(S.WAITING_FOR_HUMAN)
    assert S.WAITING_FOR_HUMAN not in IN_PROGRESS_STATES


@pytest.mark.parametrize("state", sorted(IN_PROGRESS_STATES))
def test_every_in_progress_state_can_retry_or_fail(state: DocumentStatus) -> None:
    assert can_transition(state, S.RETRYING)
    assert can_transition(state, S.FAILED)


def test_retry_requeues_and_failure_allows_manual_reprocess() -> None:
    transition(S.RETRYING, S.QUEUED)
    transition(S.FAILED, S.QUEUED)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.RECEIVED, S.COMPLETED),
        (S.QUEUED, S.EXTRACTING),
        (S.COMPLETED, S.VALIDATING),
        (S.REJECTED, S.COMPLETED),
        (S.WAITING_FOR_HUMAN, S.COMPLETED),
        (S.EXTRACTED, S.RETRYING),
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
