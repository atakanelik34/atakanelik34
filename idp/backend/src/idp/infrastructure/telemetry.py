"""Optional OpenTelemetry tracing.

Enabled only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set *and* the optional
`otel` extra is installed; otherwise a no-op that says so once. Spans carry
route templates and ids, never document content (no request bodies captured).
"""

from __future__ import annotations

from typing import Any

from idp.config import Settings
from idp.infrastructure.logging import get_logger

log = get_logger(__name__)


def configure_tracing(app: Any, settings: Settings) -> bool:
    endpoint = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        return False
    try:
        from opentelemetry import trace  # noqa: PLC0415 — optional dependency
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import (  # noqa: PLC0415
            FastAPIInstrumentor,
        )
        from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
        from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415
    except ImportError:
        log.warning("telemetry.unavailable", reason="install the 'otel' extra to enable tracing")
        return False
    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,healthz")
    log.info("telemetry.enabled", service=settings.otel_service_name)
    return True
