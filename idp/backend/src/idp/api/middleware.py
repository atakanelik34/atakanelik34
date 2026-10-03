"""Pure ASGI middleware: correlation id, request logging, security headers."""

from __future__ import annotations

import time

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from idp.context import client_ip_var, correlation_id_var, sanitize_correlation_id
from idp.infrastructure.logging import get_logger
from idp.infrastructure.metrics import observe_http

CORRELATION_HEADER = "X-Correlation-ID"

log = get_logger("idp.http")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}

_QUIET_PATHS = frozenset({"/health/live", "/health/ready"})


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, api_prefix: str, hsts: bool) -> None:
        self.app = app
        self._quiet = {f"{api_prefix}{p}" for p in _QUIET_PATHS}
        self._hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        correlation_id = sanitize_correlation_id(headers.get(CORRELATION_HEADER))
        client = scope.get("client")
        client_ip = client[0] if client else None
        cid_token = correlation_id_var.set(correlation_id)
        ip_token = client_ip_var.set(client_ip)
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = MutableHeaders(scope=message)
                response_headers[CORRELATION_HEADER] = correlation_id
                for name, value in SECURITY_HEADERS.items():
                    response_headers.setdefault(name, value)
                if self._hsts:
                    response_headers.setdefault(
                        "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            route = scope.get("route")
            observe_http(
                str(scope.get("method", "")),
                getattr(route, "path", "unmatched"),
                status_code,
                time.perf_counter() - started,
            )
            if path not in self._quiet:
                # Path only — never the query string, which may carry signatures.
                log.info(
                    "http.request",
                    method=scope.get("method"),
                    path=path,
                    status=status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )
            structlog.contextvars.unbind_contextvars("correlation_id")
            correlation_id_var.reset(cid_token)
            client_ip_var.reset(ip_token)
