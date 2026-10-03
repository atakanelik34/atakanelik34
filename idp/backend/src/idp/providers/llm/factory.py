"""Build LLM providers and the gateway from configuration."""

from __future__ import annotations

from idp.config import Settings
from idp.domain.routing import Locality
from idp.providers.llm.adapters import (
    AnthropicProvider,
    MockLLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from idp.providers.llm.base import LLMProvider
from idp.providers.llm.gateway import LLMGateway
from idp.providers.resilience import BreakerRegistry

# Names of every LLM provider the deployment knows about, configured or not.
KNOWN_LLM_PROVIDERS = ("ollama", "openai-compatible", "anthropic", "mock-llm")


def create_llm_providers(settings: Settings) -> dict[str, LLMProvider]:
    timeout = settings.llm_timeout_seconds
    providers: dict[str, LLMProvider] = {}
    if settings.ollama_base_url and settings.ollama_model:
        providers["ollama"] = OllamaProvider(
            name="ollama",
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            declared_locality=Locality.LOCAL,
            timeout_seconds=timeout,
        )
    if settings.openai_base_url and settings.openai_model:
        providers["openai-compatible"] = OpenAICompatibleProvider(
            name="openai-compatible",
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            declared_locality=Locality(settings.openai_locality),
            timeout_seconds=timeout,
            api_key=settings.openai_api_key.get_secret_value() or None,
            cost_input_per_1k=settings.openai_cost_input_per_1k,
            cost_output_per_1k=settings.openai_cost_output_per_1k,
        )
    anthropic_key = settings.anthropic_api_key.get_secret_value()
    if anthropic_key and settings.anthropic_model:
        providers["anthropic"] = AnthropicProvider(
            name="anthropic",
            base_url=settings.anthropic_base_url,
            model=settings.anthropic_model,
            declared_locality=Locality.CLOUD,
            timeout_seconds=timeout,
            api_key=anthropic_key,
            cost_input_per_1k=settings.anthropic_cost_input_per_1k,
            cost_output_per_1k=settings.anthropic_cost_output_per_1k,
        )
    if settings.mock_llm_enabled:
        providers["mock-llm"] = MockLLMProvider()
    return providers


def create_llm_gateway(
    settings: Settings, providers: dict[str, LLMProvider] | None = None
) -> LLMGateway:
    return LLMGateway(
        create_llm_providers(settings) if providers is None else providers,
        allowed_hosts=settings.llm_allowed_host_set,
        local_hosts=settings.llm_local_host_set,
        breakers=BreakerRegistry(
            failure_threshold=settings.breaker_failure_threshold,
            reset_after_seconds=settings.breaker_reset_seconds,
        ),
        max_retries=settings.llm_max_retries,
    )
