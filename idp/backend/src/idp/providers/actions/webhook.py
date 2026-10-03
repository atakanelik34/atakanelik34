"""Signed webhook delivery (actions and outbox events).

Only allow-listed hosts are called and redirects are not followed. Every
request carries `Idempotency-Key` and an HMAC signature over timestamp + body;
the secret comes from the `IDP_SECRET_*` variable named by the connection.
2xx and 409 (already processed) count as success; 429/5xx/timeouts are
transient; other 4xx are permanent.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

import httpx

from idp.infrastructure.db.models import Connection
from idp.providers.actions.base import (
    ActionError,
    ActionOutcome,
    ActionRequest,
    canonical_json,
    sign,
)
from idp.providers.enrichment.base import WebhookConfig


class WebhookSender:
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

    async def post(
        self, connection: Connection, body: dict[str, Any], *, idempotency_key: str, event: str
    ) -> httpx.Response:
        config = WebhookConfig.model_validate(connection.config)
        host = (urlsplit(config.url).hostname or "").lower()
        if host not in self._allowed:
            raise ActionError("host_not_allowed")
        secret = os.environ.get(config.secret_env)
        if not secret:
            raise ActionError("secret_not_configured")
        raw = canonical_json(body)
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
            "X-IDP-Event": event,
            "X-IDP-Signature": sign(secret, raw),
        }
        try:
            response = await self._client.post(config.url, content=raw, headers=headers)
        except httpx.TimeoutException as exc:
            raise ActionError("timeout", transient=True) from exc
        except httpx.TransportError as exc:
            raise ActionError("connection_error", transient=True) from exc
        status = response.status_code
        if status < 300 or status == 409:
            return response
        raise ActionError(f"http_{status}", transient=status == 429 or status >= 500)


class WebhookActionProvider:
    kind = "webhook"
    is_mock = False

    def __init__(self, sender: WebhookSender) -> None:
        self._sender = sender

    def configured(self) -> bool:
        return True

    async def execute(self, connection: Connection, request: ActionRequest) -> ActionOutcome:
        response = await self._sender.post(
            connection,
            {
                "type": "action",
                "action": request.action,
                "document_id": request.document_id,
                "idempotency_key": request.idempotency_key,
                "payload": request.payload,
            },
            idempotency_key=request.idempotency_key,
            event=f"action.{request.action}",
        )
        reference = response.headers.get("X-Reference")
        try:
            body = response.json()
        except ValueError:
            body = None
        if reference is None and isinstance(body, dict):
            ref = body.get("id") or body.get("reference")
            reference = str(ref) if ref is not None else None
        return ActionOutcome(
            external_reference=reference[:200] if reference else None,
            response={"status": response.status_code},
        )
