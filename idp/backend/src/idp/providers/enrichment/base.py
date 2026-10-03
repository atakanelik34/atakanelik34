"""Enrichment provider port and connection configuration shapes.

Connection configs are validated when saved. They never contain secrets: a
REST connection names an environment variable (prefix `IDP_SECRET_`) that holds
its credential, so secrets stay in the deployment environment.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from idp.domain.matching import Match
from idp.infrastructure.db.models import Connection

ConnectionKind = Literal["master_data", "rest", "mock_erp"]


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


CONFIG_MODELS: Mapping[str, type[_Strict]] = {
    "master_data": MasterDataConfig,
    "rest": RestConfig,
    "mock_erp": MockERPConfig,
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
