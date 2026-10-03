"""Lookup through a configured REST endpoint (ERP, vendor service, …).

Only hosts in `ENRICHMENT_ALLOWED_HOSTS` are called; redirects are not
followed; credentials come from an `IDP_SECRET_*` environment variable named by
the connection. Responses are mapped to records and scored locally, so the
remote system cannot decide what counts as a match.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from idp.domain.matching import Match, Record, score_record
from idp.infrastructure.db.models import Connection
from idp.providers.enrichment.base import EnrichmentError, RestConfig

MAX_RESULTS = 50


def _dig(body: Any, path: str) -> Any:
    for part in [p for p in path.split(".") if p]:
        body = body.get(part) if isinstance(body, dict) else None
    return body


class RestLookupProvider:
    kind = "rest"
    is_mock = False

    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str],
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._allowed = allowed_hosts
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def lookup(
        self,
        session: AsyncSession,
        connection: Connection,
        entity: str,
        criteria: Mapping[str, str],
    ) -> list[Match]:
        del session, entity
        config = RestConfig.model_validate(connection.config)
        host = (urlsplit(config.base_url).hostname or "").lower()
        if host not in self._allowed:
            raise EnrichmentError("host_not_allowed")
        params = {config.query[a]: v for a, v in criteria.items() if a in config.query and v}
        if not params:
            return []
        headers = _auth_headers(config)
        try:
            response = await self._client.get(
                f"{config.base_url}{config.path}", params=params, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise EnrichmentError("timeout", transient=True) from exc
        except httpx.TransportError as exc:
            raise EnrichmentError("connection_error", transient=True) from exc
        if response.status_code == 404:
            return []
        if response.status_code >= 400:
            raise EnrichmentError(
                f"http_{response.status_code}", transient=response.status_code >= 500
            )
        try:
            items = _dig(response.json(), config.results_path)
        except ValueError as exc:
            raise EnrichmentError("invalid_json_response") from exc
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            raise EnrichmentError("unexpected_response_shape")
        return _matches(items, config, criteria)


def _auth_headers(config: RestConfig) -> dict[str, str]:
    if not (config.auth_header and config.secret_env):
        return {}
    secret = os.environ.get(config.secret_env)
    if not secret:
        raise EnrichmentError("secret_not_configured")
    return {config.auth_header: f"{config.auth_scheme} {secret}".strip()}


def _matches(items: list[Any], config: RestConfig, criteria: Mapping[str, str]) -> list[Match]:
    matches = []
    for item in items[:MAX_RESULTS]:
        if not isinstance(item, dict) or item.get(config.key_field) is None:
            continue
        record = Record(
            key=str(item[config.key_field]),
            name=str(item.get(config.name_field) or ""),
            attributes={
                a: item.get(f)
                for a, f in config.attribute_fields.items()
                if item.get(f) is not None
            },
        )
        if (match := score_record(record, criteria)) is not None:
            matches.append(match)
    return matches
