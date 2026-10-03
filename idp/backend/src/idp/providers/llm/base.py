"""LLM provider port. Adapters speak HTTP via httpx; no vendor SDKs.

Adapters only transport a request and parse a response. Policy, host
allow-listing, retries, circuit breaking and usage accounting live in the
gateway, so no adapter can be called around them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from idp.domain.routing import Locality


@dataclass(frozen=True, slots=True)
class LLMRequest:
    system: str
    user: str
    # JSON schema of the expected answer; adapters pass it on where supported.
    json_schema: dict[str, Any] | None = None
    max_output_tokens: int = 2048
    temperature: float = 0.0


@dataclass(frozen=True, slots=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    model: str
    usage: LLMUsage


class LLMCallError(Exception):
    """Transport/protocol failure. `transient` errors may be retried."""

    def __init__(self, code: str, *, transient: bool, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.transient = transient
        self.status = status


class LLMProvider(Protocol):
    name: str
    model: str
    declared_locality: Locality
    is_mock: bool
    base_url: str
    cost_input_per_1k: float
    cost_output_per_1k: float

    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    async def aclose(self) -> None: ...


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


async def post_json(
    client: httpx.AsyncClient, url: str, payload: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    """POST and decode JSON, mapping failures to `LLMCallError` (never leaking bodies)."""
    try:
        response = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise LLMCallError("timeout", transient=True) from exc
    except httpx.TransportError as exc:
        raise LLMCallError("connection_error", transient=True) from exc
    status = response.status_code
    if status == 429 or status >= 500:
        raise LLMCallError(f"http_{status}", transient=True, status=status)
    if status >= 400:
        raise LLMCallError(f"http_{status}", transient=False, status=status)
    try:
        body = response.json()
    except ValueError as exc:
        raise LLMCallError("invalid_json_response", transient=False, status=status) from exc
    if not isinstance(body, dict):
        raise LLMCallError("invalid_json_response", transient=False, status=status)
    return body


def as_int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
