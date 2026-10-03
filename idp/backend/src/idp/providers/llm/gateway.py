"""The only way to call an LLM.

Every call passes, in order: provider configured → host allow-list → policy
(locality, LLM allowed, mock allowed) → circuit breaker → bounded retries with
exponential backoff and jitter for transient errors. Each call — including
refusals — produces a `CallRecord` with tokens, cost and latency but never
prompt or response content. The router enforces the same policy earlier; this
is the second, independent check (CLAUDE.md non-negotiable 4).

Effective locality is fail-safe: a provider declared `local` whose host is not
in the local-hosts list is treated as `cloud`.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace

from idp.domain.errors import ConfigurationError, PolicyViolationError, ProviderError
from idp.domain.routing import Locality, PolicySnapshot
from idp.infrastructure.logging import get_logger
from idp.infrastructure.metrics import LLM_CALLS, LLM_TOKENS
from idp.providers.llm.base import LLMCallError, LLMProvider, LLMRequest, LLMResponse, host_of
from idp.providers.resilience import BreakerRegistry

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CallRecord:
    provider: str
    model: str
    locality: str
    purpose: str
    status: str  # ok | failed | refused
    attempts: int
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    latency_ms: int = 0
    error_code: str | None = None


class LLMGateway:
    def __init__(
        self,
        providers: Mapping[str, LLMProvider],
        *,
        allowed_hosts: frozenset[str],
        local_hosts: frozenset[str],
        breakers: BreakerRegistry | None = None,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._providers = dict(providers)
        self._allowed = frozenset(h.lower() for h in allowed_hosts)
        self._local = frozenset(h.lower() for h in local_hosts)
        self._breakers = breakers or BreakerRegistry()
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep

    @property
    def provider_names(self) -> list[str]:
        return list(self._providers)

    def provider(self, name: str) -> LLMProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ConfigurationError(f"LLM provider '{name}' is not configured") from exc

    def effective_locality(self, name: str) -> Locality:
        provider = self.provider(name)
        if provider.is_mock:
            return Locality.LOCAL
        if (
            provider.declared_locality is Locality.LOCAL
            and host_of(provider.base_url) in self._local
        ):
            return Locality.LOCAL
        return Locality.CLOUD

    def host_allowed(self, name: str) -> bool:
        provider = self.provider(name)
        return provider.is_mock or host_of(provider.base_url) in self._allowed

    def circuit_open(self, name: str) -> bool:
        return self._breakers.is_open(name)

    def refusal(self, name: str, policy: PolicySnapshot) -> str | None:
        """Why `policy` (or deployment configuration) forbids calling `name`, if it does."""
        provider = self.provider(name)
        locality = self.effective_locality(name)
        if not self.host_allowed(name):
            return f"host {host_of(provider.base_url)} is not in LLM_ALLOWED_HOSTS"
        if not policy.allow_llm:
            return "policy disallows LLM processing"
        if provider.is_mock and not policy.allow_mock_providers:
            return "mock provider disabled"
        if not policy.permits(locality):
            return f"policy {policy.mode.value} forbids {locality.value} providers"
        return None

    def _cost(self, provider: LLMProvider, response: LLMResponse) -> float:
        return round(
            response.usage.input_tokens / 1000 * provider.cost_input_per_1k
            + response.usage.output_tokens / 1000 * provider.cost_output_per_1k,
            6,
        )

    async def complete(
        self,
        name: str,
        request: LLMRequest,
        *,
        policy: PolicySnapshot,
        purpose: str,
        sink: list[CallRecord],
    ) -> LLMResponse:
        before = len(sink)
        try:
            return await self._complete(name, request, policy=policy, purpose=purpose, sink=sink)
        finally:
            for call in sink[before:]:
                LLM_CALLS.labels(call.provider, call.purpose, call.status).inc()
                LLM_TOKENS.labels(call.provider, "input").inc(call.input_tokens)
                LLM_TOKENS.labels(call.provider, "output").inc(call.output_tokens)

    async def _complete(
        self,
        name: str,
        request: LLMRequest,
        *,
        policy: PolicySnapshot,
        purpose: str,
        sink: list[CallRecord],
    ) -> LLMResponse:
        provider = self.provider(name)
        locality = self.effective_locality(name).value
        record = CallRecord(
            provider=name,
            model=provider.model,
            locality=locality,
            purpose=purpose,
            status="",
            attempts=0,
        )
        reason = self.refusal(name, policy)
        if reason is not None:
            sink.append(
                replace(record, status="refused", attempts=0, error_code="policy_violation")
            )
            log.warning("llm.refused", provider=name, purpose=purpose, reason=reason)
            raise PolicyViolationError(f"LLM call refused: {reason}", details={"provider": name})
        breaker = self._breakers.get(name)
        if not breaker.allow():
            sink.append(replace(record, status="refused", attempts=0, error_code="circuit_open"))
            raise ProviderError("LLM provider circuit is open", details={"provider": name})

        started = time.monotonic()
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await provider.complete(request)
            except LLMCallError as exc:
                if exc.transient and attempt <= self._max_retries:
                    delay = self._backoff * 2 ** (attempt - 1) * (1 + random.random() / 2)  # noqa: S311 — jitter
                    await self._sleep(delay)
                    continue
                breaker.record_failure()
                sink.append(
                    replace(
                        record,
                        status="failed",
                        attempts=attempt,
                        latency_ms=round((time.monotonic() - started) * 1000),
                        error_code=exc.code,
                    )
                )
                log.warning(
                    "llm.failed", provider=name, purpose=purpose, error=exc.code, attempts=attempt
                )
                raise ProviderError(
                    "LLM provider call failed", details={"provider": name, "error": exc.code}
                ) from exc
            breaker.record_success()
            sink.append(
                replace(
                    record,
                    status="ok",
                    attempts=attempt,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cost=self._cost(provider, response),
                    latency_ms=round((time.monotonic() - started) * 1000),
                )
            )
            return response

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
