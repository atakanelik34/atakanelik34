"""Request/job-scoped context (correlation id, client ip) shared by all layers.

Set by the HTTP middleware or the worker job wrapper; read by audit logging,
structured logs and event emission. Contains IDs only — never document data.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9\-_.]{8,64}$")

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def sanitize_correlation_id(value: str | None) -> str:
    """Accept a caller-supplied id only if it is well-formed; otherwise mint one."""
    if value and _CORRELATION_ID_PATTERN.fullmatch(value):
        return value
    return new_correlation_id()


def current_correlation_id() -> str | None:
    return correlation_id_var.get()


def current_client_ip() -> str | None:
    return client_ip_var.get()
