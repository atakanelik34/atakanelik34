"""Classified error hierarchy.

Every error raised deliberately by the platform carries an `ErrorCategory`. The
API maps categories to HTTP responses; workers use `retryable` to decide
between RETRYING and FAILED. Nothing is reported as an "unknown error".
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    SYSTEM_ERROR = "SYSTEM_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    DOCUMENT_ERROR = "DOCUMENT_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    BUSINESS_ERROR = "BUSINESS_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"


RETRYABLE_CATEGORIES = frozenset({ErrorCategory.SYSTEM_ERROR, ErrorCategory.PROVIDER_ERROR})


class IDPError(Exception):
    """Base class for all classified platform errors.

    `message` must be safe to show to API clients: never include document
    contents, secrets, or internal paths.
    """

    category: ErrorCategory = ErrorCategory.SYSTEM_ERROR
    code: str = "system_error"
    # Overrides the category's default HTTP status where HTTP has a precise code.
    http_status: int | None = None

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    @property
    def retryable(self) -> bool:
        return self.category in RETRYABLE_CATEGORIES


class InternalError(IDPError):
    category = ErrorCategory.SYSTEM_ERROR
    code = "system_error"


class ProviderError(IDPError):
    category = ErrorCategory.PROVIDER_ERROR
    code = "provider_error"


class DocumentError(IDPError):
    category = ErrorCategory.DOCUMENT_ERROR
    code = "document_error"


class ValidationError(IDPError):
    category = ErrorCategory.VALIDATION_ERROR
    code = "validation_error"


class BusinessError(IDPError):
    category = ErrorCategory.BUSINESS_ERROR
    code = "business_error"


class InvalidStateTransitionError(BusinessError):
    code = "invalid_state_transition"


class AuthenticationError(IDPError):
    category = ErrorCategory.AUTHENTICATION_ERROR
    code = "authentication_failed"


class AuthorizationError(IDPError):
    category = ErrorCategory.AUTHORIZATION_ERROR
    code = "forbidden"


class NotFoundError(IDPError):
    category = ErrorCategory.NOT_FOUND
    code = "not_found"


class ConfigurationError(IDPError):
    category = ErrorCategory.CONFIGURATION_ERROR
    code = "configuration_error"


class ConflictError(BusinessError):
    code = "conflict"


class RateLimitedError(IDPError):
    category = ErrorCategory.RATE_LIMITED
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(message, details={"retry_after_seconds": retry_after_seconds})
        self.retry_after_seconds = retry_after_seconds


class PayloadTooLargeError(ValidationError):
    code = "payload_too_large"
    http_status = 413


class LengthRequiredError(ValidationError):
    code = "length_required"
    http_status = 411


class UnsupportedMediaTypeError(DocumentError):
    code = "unsupported_media_type"
    http_status = 415


class DuplicateDocumentError(ConflictError):
    code = "duplicate_document"


class PolicyViolationError(BusinessError):
    """A call the tenant's processing policy (or the deployment) does not permit."""

    code = "policy_violation"
