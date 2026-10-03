"""Audit trail writer.

Audit rows are written in the caller's transaction so a state change and its
audit record commit (or roll back) together.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from idp.context import current_client_ip, current_correlation_id
from idp.domain.identity import Principal
from idp.infrastructure.db.models import AuditLog


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"


class AuditAction(StrEnum):
    AUTH_LOGIN_SUCCEEDED = "auth.login_succeeded"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    USER_CREATED = "user.created"
    TENANT_BOOTSTRAPPED = "tenant.bootstrapped"
    DOCUMENT_RECEIVED = "document.received"
    DOCUMENT_DUPLICATE_REJECTED = "document.duplicate_rejected"
    DOCUMENT_STATUS_CHANGED = "document.status_changed"
    DOCUMENT_DOWNLOADED = "document.downloaded"
    DOCUMENT_DELETED = "document.deleted"
    DOCUMENT_PROCESSING_REQUESTED = "document.processing_requested"
    JOB_FAILED = "job.failed"
    JOB_DEAD_LETTERED = "job.dead_lettered"
    DOCUMENT_TYPE_CREATED = "document_type.created"
    DOCUMENT_TYPE_UPDATED = "document_type.updated"
    SCHEMA_DRAFT_SAVED = "schema.draft_saved"
    SCHEMA_PUBLISHED = "schema.published"
    REVIEW_REQUESTED = "review.requested"
    REVIEW_CLAIMED = "review.claimed"
    REVIEW_FIELD_ACCEPTED = "review.field_accepted"
    REVIEW_FIELD_CORRECTED = "review.field_corrected"
    REVIEW_FIELD_REJECTED = "review.field_rejected"
    REVIEW_ROW_ADDED = "review.row_added"
    REVIEW_ROW_DELETED = "review.row_deleted"
    REVIEW_APPROVED = "review.approved"
    REVIEW_REJECTED = "review.rejected"
    REVIEW_SENT_BACK = "review.sent_back"
    EVALUATION_DATASET_CREATED = "evaluation.dataset_created"
    EVALUATION_DATASET_DELETED = "evaluation.dataset_deleted"
    EVALUATION_ITEMS_IMPORTED = "evaluation.items_imported"
    EVALUATION_RUN_COMPLETED = "evaluation.run_completed"
    POLICY_UPDATED = "policy.updated"


class AuditEntity(StrEnum):
    USER = "user"
    TENANT = "tenant"
    DOCUMENT = "document"
    JOB = "job"
    DOCUMENT_TYPE = "document_type"
    REVIEW_TASK = "review_task"
    EXTRACTED_FIELD = "extracted_field"
    EVALUATION_DATASET = "evaluation_dataset"
    PROCESSING_POLICY = "processing_policy"


def record_audit(
    session: AsyncSession,
    *,
    action: AuditAction,
    entity_type: AuditEntity,
    entity_id: uuid.UUID | str | None,
    tenant_id: uuid.UUID | None,
    actor: Principal | None = None,
    actor_type: ActorType | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    resolved_actor_type = actor_type or (ActorType.USER if actor else ActorType.ANONYMOUS)
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_type=resolved_actor_type.value,
        actor_id=actor.user_id if actor else None,
        action=action.value,
        entity_type=entity_type.value,
        entity_id=str(entity_id) if entity_id is not None else None,
        before=before,
        after=after,
        correlation_id=current_correlation_id(),
        ip_address=current_client_ip(),
    )
    session.add(entry)
    return entry
