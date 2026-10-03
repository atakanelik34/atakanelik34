"""The provider catalog: which processing providers this deployment has.

One source of truth for the worker (which instantiates them) and the API
(which describes them). Unconfigured providers are listed as such — never
replaced by a fake. Phase 9 adds LLM providers here.
"""

from __future__ import annotations

from dataclasses import dataclass

from idp.config import Settings
from idp.domain.routing import Locality
from idp.providers.extraction.base import ExtractionProvider
from idp.providers.extraction.key_value import KeyValueExtractor
from idp.providers.extraction.regex_extractor import RegexExtractor
from idp.providers.extraction.table import TableExtractor
from idp.providers.ocr.factory import create_ocr_engine


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    kind: str  # ocr | extraction | llm | enrichment | action
    name: str
    version: str
    method: str
    tier: int | None
    locality: str
    is_mock: bool
    status: str  # configured | not_configured
    cost_per_page: float = 0.0


def extraction_providers(settings: Settings) -> list[ExtractionProvider]:
    del settings  # deterministic providers need no configuration
    return [RegexExtractor(), KeyValueExtractor(), TableExtractor()]


def describe_providers(settings: Settings) -> list[ProviderDescriptor]:
    ocr = create_ocr_engine(settings)
    out = [
        ProviderDescriptor(
            kind="ocr",
            name=ocr.name if ocr else settings.ocr_engine,
            version="" if ocr is None else getattr(ocr, "version", ""),
            method="ocr",
            tier=None,
            locality=ocr.locality if ocr else Locality.LOCAL.value,
            is_mock=bool(ocr and ocr.is_mock),
            status="configured" if ocr else "not_configured",
        )
    ]
    for p in extraction_providers(settings):
        out.append(
            ProviderDescriptor(
                kind="extraction",
                name=p.info.name,
                version=p.info.version,
                method=p.info.method,
                tier=int(p.info.tier),
                locality=p.info.locality.value,
                is_mock=p.info.is_mock,
                status="configured",
                cost_per_page=p.info.cost_per_page,
            )
        )
    return out
