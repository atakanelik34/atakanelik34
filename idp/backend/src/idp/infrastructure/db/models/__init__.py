"""ORM models. Import every model here so Alembic sees the full metadata."""

from idp.infrastructure.db.models.audit import AuditLog
from idp.infrastructure.db.models.documents import (
    Document,
    DocumentPage,
    ProcessingJob,
    ProcessingStep,
)
from idp.infrastructure.db.models.enrichment import (
    Connection,
    EnrichmentResult,
    MasterDataRecord,
)
from idp.infrastructure.db.models.evaluation import (
    EvaluationDataset,
    EvaluationItem,
    EvaluationRun,
)
from idp.infrastructure.db.models.extraction import ExtractedField, ExtractionResult
from idp.infrastructure.db.models.identity import Project, Tenant, User
from idp.infrastructure.db.models.policy import ProcessingPolicy, ProviderCall
from idp.infrastructure.db.models.review import ReviewAction, ReviewTask, ValidationResult
from idp.infrastructure.db.models.taxonomy import (
    DocumentPart,
    DocumentType,
    SchemaField,
    SchemaVersion,
)

__all__ = [
    "AuditLog",
    "Connection",
    "Document",
    "DocumentPage",
    "DocumentPart",
    "DocumentType",
    "EnrichmentResult",
    "EvaluationDataset",
    "EvaluationItem",
    "EvaluationRun",
    "ExtractedField",
    "ExtractionResult",
    "MasterDataRecord",
    "ProcessingJob",
    "ProcessingPolicy",
    "ProcessingStep",
    "Project",
    "ProviderCall",
    "ReviewAction",
    "ReviewTask",
    "SchemaField",
    "SchemaVersion",
    "Tenant",
    "User",
    "ValidationResult",
]
