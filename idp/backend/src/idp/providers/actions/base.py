"""Action provider port: execute one configured business action, idempotently."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from idp.infrastructure.db.models import Connection


@dataclass(frozen=True, slots=True)
class ActionRequest:
    idempotency_key: str
    action: str
    document_id: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    external_reference: str | None = None
    response: dict[str, Any] = field(default_factory=dict)


class ActionError(Exception):
    """`transient` errors are retried by the job; others fail the run."""

    def __init__(self, code: str, *, transient: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.transient = transient


class ActionProvider(Protocol):
    kind: str
    is_mock: bool

    def configured(self) -> bool: ...

    async def execute(self, connection: Connection, request: ActionRequest) -> ActionOutcome: ...


def canonical_json(body: Any) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    """`t=<unix>,v1=<hex HMAC-SHA256 of "<t>.<body>">` (receivers verify and reject old t)."""
    ts = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"
