"""Enrichment provider port and connection configuration shapes.

Connection configs are validated when saved. They never contain secrets: a
REST connection names an environment variable (prefix `IDP_SECRET_`) that holds
its credential, so secrets stay in the deployment environment.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from idp.domain.matching import Match
from idp.infrastructure.db.models import Connection

ConnectionKind = Literal["master_data", "rest", "mock_erp", "webhook", "email"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MasterDataConfig(_Strict):
    pass


class MockERPConfig(_Strict):
    pass


class RestConfig(_Strict):
    base_url: str = Field(max_length=300)
    path: str = Field(default="/", pattern=r"^/[A-Za-z0-9_\-/.]*$", max_length=200)
    # lookup attribute (tax_id, iban, name, key, …) -> query parameter name
    query: dict[str, str] = Field(min_length=1, max_length=10)
    results_path: str = Field(default="", pattern=r"^[A-Za-z0-9_.]*$", max_length=100)
    key_field: str = Field(default="id", max_length=64)
    name_field: str = Field(default="name", max_length=64)
    # record attribute -> response field
    attribute_fields: dict[str, str] = Field(default_factory=dict, max_length=30)
    auth_header: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
    auth_scheme: str = Field(default="Bearer", max_length=20)
    secret_env: str | None = Field(default=None, pattern=r"^IDP_SECRET_[A-Z0-9_]{1,60}$")

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("base_url must be an http(s) URL")
        if parts.username or parts.password:
            raise ValueError("credentials do not belong in base_url; use secret_env")
        return value.rstrip("/")


class WebhookConfig(_Strict):
    """Outbound webhook (actions and event subscriptions). Signed with HMAC-SHA256."""

    url: str = Field(max_length=500)
    # Environment variable (IDP_SECRET_*) holding the signing secret.
    secret_env: str = Field(pattern=r"^IDP_SECRET_[A-Z0-9_]{1,60}$")
    # Outbox events to deliver to this endpoint (empty: actions only).
    events: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("url must be an http(s) URL")
        if parts.username or parts.password:
            raise ValueError("credentials do not belong in the URL; use secret_env")
        return value

    @field_validator("events")
    @classmethod
    def _events(cls, events: list[str]) -> list[str]:
        unknown = set(events) - set(EVENT_TYPES)
        if unknown:
            raise ValueError(f"unknown event types: {', '.join(sorted(unknown))}")
        return events


class EmailConfig(_Strict):
    """Notification e-mail to a fixed, configured recipient list (never from documents)."""

    to: list[str] = Field(min_length=1, max_length=10)
    subject_prefix: str = Field(default="[IDP]", max_length=60)

    @field_validator("to")
    @classmethod
    def _addresses(cls, to: list[str]) -> list[str]:
        for address in to:
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address):
                raise ValueError(f"invalid e-mail address '{address}'")
        return to


EVENT_TYPES = (
    "document.completed",
    "document.failed",
    "document.rejected",
    "document.waiting_for_human",
    "document.ready_for_action",
    "action.succeeded",
    "action.failed",
)

# Connection kinds usable for lookups (enrichment) and for actions.
LOOKUP_KINDS = frozenset({"master_data", "rest", "mock_erp"})
ACTION_KINDS = frozenset({"webhook", "email", "mock_erp"})

CONFIG_MODELS: Mapping[str, type[_Strict]] = {
    "master_data": MasterDataConfig,
    "rest": RestConfig,
    "mock_erp": MockERPConfig,
    "webhook": WebhookConfig,
    "email": EmailConfig,
}


class EnrichmentError(Exception):
    def __init__(self, code: str, *, transient: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.transient = transient


class EnrichmentProvider(Protocol):
    kind: str
    is_mock: bool

    async def lookup(
        self,
        session: AsyncSession,
        connection: Connection,
        entity: str,
        criteria: Mapping[str, str],
    ) -> list[Match]: ...
