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
from idp.providers.extraction.llm import LLMExtractor
from idp.providers.extraction.regex_extractor import RegexExtractor
from idp.providers.extraction.table import TableExtractor
from idp.providers.llm.factory import KNOWN_LLM_PROVIDERS, create_llm_gateway
from idp.providers.llm.gateway import LLMGateway
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
    status: str  # configured | not_configured | host_not_allowed
    cost_per_page: float = 0.0


def extraction_providers(settings: Settings, gateway: LLMGateway) -> list[ExtractionProvider]:
    """Deterministic extractors plus one LLM extractor per configured LLM provider."""
    providers: list[ExtractionProvider] = [RegexExtractor(), KeyValueExtractor(), TableExtractor()]
    providers.extend(
        LLMExtractor(gateway, name, max_input_chars=settings.llm_max_input_chars)
        for name in gateway.provider_names
    )
    return providers


async def describe_providers(settings: Settings) -> list[ProviderDescriptor]:
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
    gateway = create_llm_gateway(settings)
    for name in KNOWN_LLM_PROVIDERS:
        if name in gateway.provider_names:
            llm = gateway.provider(name)
            out.append(
                ProviderDescriptor(
                    kind="llm",
                    name=name,
                    version=llm.model,
                    method="llm",
                    tier=None,
                    locality=gateway.effective_locality(name).value,
                    is_mock=llm.is_mock,
                    status="configured" if gateway.host_allowed(name) else "host_not_allowed",
                    cost_per_page=0.0,
                )
            )
        else:
            out.append(
                ProviderDescriptor(
                    kind="llm",
                    name=name,
                    version="",
                    method="llm",
                    tier=None,
                    locality=Locality.CLOUD.value if name == "anthropic" else "unknown",
                    is_mock=name == "mock-llm",
                    status="not_configured",
                )
            )
    try:
        extractors = extraction_providers(settings, gateway)
    finally:
        await gateway.aclose()  # descriptions only: no calls are made
    for p in extractors:
        out.append(
            ProviderDescriptor(
                kind="extraction",
                name=p.info.name,
                version=p.info.version,
                method=p.info.method,
                tier=int(p.info.tier),
                locality=p.info.locality.value,
                is_mock=p.info.is_mock,
                status="configured" if p.info.configured else "host_not_allowed",
                cost_per_page=p.info.cost_per_page,
            )
        )
    return out
