"""Retry policy for processing jobs (pure)."""

from __future__ import annotations

import random
from dataclasses import dataclass

from idp.domain.errors import RETRYABLE_CATEGORIES, ErrorCategory
from idp.domain.lifecycle import JobStatus

# Jitter only spreads retries out; it is not security-sensitive.
_JITTER_RNG = random.Random()  # noqa: S311


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base_seconds: float
    max_seconds: float
    jitter_ratio: float = 0.2

    def backoff_seconds(self, attempt: int, *, rng: random.Random | None = None) -> float:
        """Delay before attempt `attempt + 1`: base * 2^(attempt-1), capped, with jitter."""
        exponent = max(attempt - 1, 0)
        delay = min(self.base_seconds * (2**exponent), self.max_seconds)
        generator = rng or _JITTER_RNG
        jitter = generator.uniform(-self.jitter_ratio, self.jitter_ratio)
        return float(max(0.0, delay * (1 + jitter)))

    def outcome(self, category: ErrorCategory, attempts: int) -> JobStatus:
        """Job status after a failed attempt number `attempts`."""
        if category not in RETRYABLE_CATEGORIES:
            return JobStatus.FAILED
        if attempts >= self.max_attempts:
            return JobStatus.DEAD_LETTERED
        return JobStatus.RETRY_SCHEDULED
