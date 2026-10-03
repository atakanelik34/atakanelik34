"""Transactional outbox and its relay (first consumer: subscribed webhooks).

`emit` adds an event in the caller's transaction, so an event exists exactly
when the change it describes was committed. The relay (a worker cron) claims
due events with a short lease, delivers each to the tenant's active webhook
connections subscribed to its type, and records per-subscriber success so a
retry never re-sends to a subscriber that already accepted it. Delivery is
at-least-once; receivers deduplicate on the event id (`Idempotency-Key`).
Payloads carry identifiers and statuses only — never document content.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from idp.infrastructure.db.models import Connection, OutboxEvent
from idp.infrastructure.logging import get_logger
from idp.providers.actions.base import ActionError
from idp.providers.actions.webhook import WebhookSender

log = get_logger(__name__)

CLAIM_LEASE = timedelta(minutes=2)


def emit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    aggregate_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    session.add(
        OutboxEvent(
            tenant_id=tenant_id,
            event_type=event_type,
            aggregate_id=aggregate_id,
            payload={"occurred_at": datetime.now(UTC).isoformat(), **payload},
            deliveries={},
        )
    )


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(3600, 10 * 2 ** min(attempts, 10)))


class OutboxRelay:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sender: WebhookSender,
        *,
        batch_size: int = 50,
        max_attempts: int = 10,
    ) -> None:
        self._sf = session_factory
        self._sender = sender
        self._batch = batch_size
        self._max_attempts = max_attempts

    async def _claim(self) -> list[uuid.UUID]:
        async with self._sf() as session, session.begin():
            ids = (
                await session.scalars(
                    select(OutboxEvent.id)
                    .where(
                        OutboxEvent.published_at.is_(None),
                        OutboxEvent.next_attempt_at <= datetime.now(UTC),
                    )
                    .order_by(OutboxEvent.created_at)
                    .limit(self._batch)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            if ids:
                await session.execute(
                    update(OutboxEvent)
                    .where(OutboxEvent.id.in_(ids))
                    .values(next_attempt_at=datetime.now(UTC) + CLAIM_LEASE)
                )
        return list(ids)

    async def relay(self) -> int:
        """Deliver due events. Returns how many were fully published."""
        published = 0
        for event_id in await self._claim():
            published += await self._deliver(event_id)
        return published

    async def _deliver(self, event_id: uuid.UUID) -> int:
        async with self._sf() as session:
            event = await session.get(OutboxEvent, event_id)
            if event is None or event.published_at is not None:
                return 0
            subscribers = [
                c
                for c in (
                    await session.scalars(
                        select(Connection).where(
                            Connection.tenant_id == event.tenant_id,
                            Connection.kind == "webhook",
                            Connection.is_active.is_(True),
                        )
                    )
                ).all()
                if event.event_type in c.config.get("events", [])
            ]
            await session.commit()
            deliveries = dict(event.deliveries)
            error: str | None = None
            for connection in subscribers:
                if deliveries.get(str(connection.id)) == "ok":
                    continue
                try:
                    await self._sender.post(
                        connection,
                        {
                            "id": str(event.id),
                            "type": event.event_type,
                            "tenant_id": str(event.tenant_id),
                            "data": event.payload,
                        },
                        idempotency_key=str(event.id),
                        event=event.event_type,
                    )
                    deliveries[str(connection.id)] = "ok"
                except ActionError as exc:
                    error = f"{connection.key}: {exc.code}"
            event.deliveries = deliveries
            event.attempts += 1
            if error is None:
                event.published_at = datetime.now(UTC)
                event.last_error = None
            elif event.attempts >= self._max_attempts:
                event.published_at = datetime.now(UTC)
                event.last_error = f"abandoned after {event.attempts} attempts; {error}"[:200]
                log.warning("outbox.abandoned", event_id=str(event.id), error=error)
            else:
                event.next_attempt_at = datetime.now(UTC) + _backoff(event.attempts)
                event.last_error = error[:200]
            await session.commit()
            return 1 if error is None else 0
