"""The single place a document's status changes: validated and audited together."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.audit import ActorType, AuditAction, AuditEntity, record_audit
from idp.domain.identity import Principal
from idp.domain.lifecycle import DocumentStatus, transition
from idp.infrastructure.db.models import Document


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
