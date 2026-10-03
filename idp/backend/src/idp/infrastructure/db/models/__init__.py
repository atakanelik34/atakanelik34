"""ORM models. Import every model here so Alembic sees the full metadata."""

from idp.infrastructure.db.models.audit import AuditLog
from idp.infrastructure.db.models.documents import (
    Document,
    DocumentPage,
    ProcessingJob,
    ProcessingStep,
)
from idp.infrastructure.db.models.identity import Project, Tenant, User
from idp.infrastructure.db.models.taxonomy import (
    DocumentPart,
    DocumentType,
    SchemaField,
    SchemaVersion,
)

__all__ = [
    "AuditLog",
    "Document",
    "DocumentPage",
    "DocumentPart",
    "DocumentType",
    "ProcessingJob",
    "ProcessingStep",
    "Project",
    "SchemaField",
    "SchemaVersion",
    "Tenant",
    "User",
]
