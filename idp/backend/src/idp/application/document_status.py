"""The single place a document's status changes: validated and audited together."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.audit import ActorType, AuditAction, AuditEntity, record_audit
from idp.application.outbox import emit
from idp.domain.identity import Principal
from idp.domain.lifecycle import DocumentStatus, transition
from idp.infrastructure.db.models import Document

# Status changes other systems may subscribe to (outbox events).
PUBLISHED_STATUSES: dict[DocumentStatus, str] = {
    DocumentStatus.COMPLETED: "document.completed",
    DocumentStatus.FAILED: "document.failed",
    DocumentStatus.REJECTED: "document.rejected",
    DocumentStatus.WAITING_FOR_HUMAN: "document.waiting_for_human",
    DocumentStatus.READY_FOR_ACTION: "document.ready_for_action",
}


def change_document_status(
    session: AsyncSession,
    document: Document,
    target: DocumentStatus,
    *,
    reason: str,
    actor: Principal | None = None,
) -> None:
    """Apply a lifecycle transition and its audit row in the caller's transaction."""
    record = transition(document.status, target, reason=reason)
    document.status = target
    record_audit(
        session,
        action=AuditAction.DOCUMENT_STATUS_CHANGED,
        entity_type=AuditEntity.DOCUMENT,
        entity_id=document.id,
        tenant_id=document.tenant_id,
        actor=actor,
        actor_type=None if actor else ActorType.SYSTEM,
        before={"status": record.from_status.value},
        after={"status": record.to_status.value, "reason": reason},
    )
    event = PUBLISHED_STATUSES.get(target)
    if event is not None:
        emit(
            session,
            tenant_id=document.tenant_id,
            event_type=event,
            aggregate_id=document.id,
            payload={"document_id": str(document.id), "status": target.value},
        )
