"""Per-provider resilience: an in-process circuit breaker.

Closed → (N consecutive failures) → open → (cool-down elapsed) → half-open,
where one trial call decides between closed and open again. State is per worker
process (ARCHITECTURE.md §6): shared state in Redis only if per-worker breakers
prove insufficient. Timeouts and retries are the caller's concern; the breaker
only counts outcomes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(slots=True)
class CircuitBreaker:
    failure_threshold: int = 5
    reset_after_seconds: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _failures: int = 0
    _opened_at: float | None = None
    _trial_in_flight: bool = False

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self.clock() - self._opened_at >= self.reset_after_seconds:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def allow(self) -> bool:
        """Whether a call may proceed now. Half-open admits a single trial."""
        state = self.state
        if state is BreakerState.CLOSED:
            return True
        if state is BreakerState.HALF_OPEN and not self._trial_in_flight:
            self._trial_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._trial_in_flight = False

    def record_failure(self) -> None:
        self._trial_in_flight = False
        if self._opened_at is not None:
            self._opened_at = self.clock()  # failed trial: stay open, restart cool-down
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self.clock()


@dataclass(slots=True)
class BreakerRegistry:
    failure_threshold: int = 5
    reset_after_seconds: float = 60.0
    _breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    def get(self, name: str) -> CircuitBreaker:
        breaker = self._breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(self.failure_threshold, self.reset_after_seconds)
            self._breakers[name] = breaker
        return breaker

    def is_open(self, name: str) -> bool:
        return self.get(name).state is BreakerState.OPEN

    def states(self) -> dict[str, str]:
        return {name: b.state.value for name, b in self._breakers.items()}
