"""HTTP adapters: OpenAI-compatible, Anthropic Messages, Ollama, and a labelled mock."""

from __future__ import annotations

import json
from typing import Any

import httpx

from idp.domain.routing import Locality
from idp.providers.llm.base import (
    LLMCallError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    as_int,
    post_json,
)


class _HttpAdapter:
    is_mock = False

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        declared_locality: Locality,
        timeout_seconds: float,
        cost_input_per_1k: float = 0.0,
        cost_output_per_1k: float = 0.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.declared_locality = declared_locality
        self.cost_input_per_1k = cost_input_per_1k
        self.cost_output_per_1k = cost_output_per_1k
        # Redirects are not followed: a redirect could leave the allow-listed host.
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)

    async def aclose(self) -> None:
        await self._client.aclose()


class OpenAICompatibleProvider(_HttpAdapter):
    """Any `/chat/completions` server: vLLM, LM Studio, llama.cpp, OpenAI, Azure-style gateways."""

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key

    async def complete(self, request: LLMRequest) -> LLMResponse:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        body = await post_json(
            self._client,
            f"{self.base_url}/chat/completions",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                "temperature": request.temperature,
                "max_tokens": request.max_output_tokens,
                "response_format": {"type": "json_object"},
            },
            headers,
        )
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMCallError("unexpected_response_shape", transient=False) from exc
        usage = body.get("usage") or {}
        return LLMResponse(
            text=str(text or ""),
            model=str(body.get("model") or self.model),
            usage=LLMUsage(
                as_int(usage.get("prompt_tokens")), as_int(usage.get("completion_tokens"))
            ),
        )


class AnthropicProvider(_HttpAdapter):
    """Anthropic Messages API (`/v1/messages`)."""

    API_VERSION = "2023-06-01"

    def __init__(self, *, api_key: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body = await post_json(
            self._client,
            f"{self.base_url}/v1/messages",
            {
                "model": self.model,
                "max_tokens": request.max_output_tokens,
                "temperature": request.temperature,
                "system": request.system,
                "messages": [{"role": "user", "content": request.user}],
            },
            {"x-api-key": self._api_key, "anthropic-version": self.API_VERSION},
        )
        blocks = body.get("content")
        if not isinstance(blocks, list):
            raise LLMCallError("unexpected_response_shape", transient=False)
        text = "".join(
            str(b.get("text", ""))
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        )
        usage = body.get("usage") or {}
        return LLMResponse(
            text=text,
            model=str(body.get("model") or self.model),
            usage=LLMUsage(as_int(usage.get("input_tokens")), as_int(usage.get("output_tokens"))),
        )


class OllamaProvider(_HttpAdapter):
    """Ollama `/api/chat` with structured output (`format` = JSON schema)."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body = await post_json(
            self._client,
            f"{self.base_url}/api/chat",
            {
                "model": self.model,
                "stream": False,
                "format": request.json_schema or "json",
                "options": {
                    "temperature": request.temperature,
                    "num_predict": request.max_output_tokens,
                },
                "messages": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
            },
            {},
        )
        message = body.get("message")
        if not isinstance(message, dict):
            raise LLMCallError("unexpected_response_shape", transient=False)
        return LLMResponse(
            text=str(message.get("content") or ""),
            model=str(body.get("model") or self.model),
            usage=LLMUsage(as_int(body.get("prompt_eval_count")), as_int(body.get("eval_count"))),
        )


class MockLLMProvider:
    """Development double. Clearly labelled; answers "nothing found" for every field.

    It never invents values: extraction through it yields no candidates, so
    documents still go to review. It exists to exercise routing, policy and
    accounting without a model.
    """

    name = "mock-llm"
    model = "mock"
    declared_locality = Locality.LOCAL
    is_mock = True
    base_url = "mock://local"
    cost_input_per_1k = 0.0
    cost_output_per_1k = 0.0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        del request
        return LLMResponse(
            text=json.dumps({"fields": {}, "rows": {}, "document_type": None, "_mock": True}),
            model=self.model,
            usage=LLMUsage(0, 0),
        )

    async def aclose(self) -> None:
        return None
