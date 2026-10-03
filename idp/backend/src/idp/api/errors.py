"""Map classified errors to RFC 9457 problem+json responses.

Clients receive a category, a stable code, a safe message and the correlation
id. Stack traces and internal details never leave the process.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from idp.context import current_correlation_id
from idp.domain.errors import ErrorCategory, IDPError, RateLimitedError
from idp.infrastructure.logging import get_logger

log = get_logger(__name__)

PROBLEM_JSON = "application/problem+json"

CATEGORY_STATUS: dict[ErrorCategory, int] = {
    ErrorCategory.SYSTEM_ERROR: 500,
    ErrorCategory.PROVIDER_ERROR: 502,
    ErrorCategory.DOCUMENT_ERROR: 422,
    ErrorCategory.VALIDATION_ERROR: 422,
    ErrorCategory.BUSINESS_ERROR: 409,
    ErrorCategory.AUTHENTICATION_ERROR: 401,
    ErrorCategory.AUTHORIZATION_ERROR: 403,
    ErrorCategory.NOT_FOUND: 404,
    ErrorCategory.CONFIGURATION_ERROR: 503,
    ErrorCategory.RATE_LIMITED: 429,
}

_HTTP_STATUS_CATEGORY: dict[int, ErrorCategory] = {
    401: ErrorCategory.AUTHENTICATION_ERROR,
    403: ErrorCategory.AUTHORIZATION_ERROR,
    404: ErrorCategory.NOT_FOUND,
    405: ErrorCategory.VALIDATION_ERROR,
    411: ErrorCategory.VALIDATION_ERROR,
    415: ErrorCategory.DOCUMENT_ERROR,
    413: ErrorCategory.VALIDATION_ERROR,
    429: ErrorCategory.RATE_LIMITED,
}


def problem(
    *,
    status: int,
    category: ErrorCategory,
    code: str,
    detail: str,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"urn:idp:error:{code}",
        "title": category.value,
        "status": status,
        "detail": detail,
        "error_category": category.value,
        "code": code,
        "correlation_id": current_correlation_id(),
    }
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


async def _idp_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, IDPError):
        return await _unhandled_handler(_request, exc)
    status = exc.http_status or CATEGORY_STATUS[exc.category]
    headers: dict[str, str] = {}
    if isinstance(exc, RateLimitedError):
        headers["Retry-After"] = str(exc.retry_after_seconds)
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if status >= 500:
        log.error("request.failed", category=exc.category.value, code=exc.code, exc_info=exc)
    return problem(
        status=status,
        category=exc.category,
        code=exc.code,
        detail=exc.message,
        extra={"details": exc.details} if exc.details else None,
        headers=headers or None,
    )


async def _validation_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        return await _unhandled_handler(_request, exc)
    # Strip submitted values ("input") so passwords etc. are never echoed back.
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()
    ]
    return problem(
        status=422,
        category=ErrorCategory.VALIDATION_ERROR,
        code="request_validation_failed",
        detail="Request validation failed",
        extra={"errors": errors},
    )


async def _http_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        return await _unhandled_handler(_request, exc)
    category = _HTTP_STATUS_CATEGORY.get(exc.status_code, ErrorCategory.SYSTEM_ERROR)
    return problem(
        status=exc.status_code,
        category=category,
        code=f"http_{exc.status_code}",
        detail=str(exc.detail),
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
    log.exception("request.unhandled_error", exc_info=exc)
    return problem(
        status=500,
        category=ErrorCategory.SYSTEM_ERROR,
        code="internal_error",
        detail="An internal error occurred",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(IDPError, _idp_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
