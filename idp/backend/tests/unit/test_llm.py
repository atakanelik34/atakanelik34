import json
from typing import Any

import httpx
import pytest

from idp.domain.classification import TypeCandidate
from idp.domain.errors import PolicyViolationError, ProviderError
from idp.domain.geometry import BBox, Block, Line, PageLayout, TextSource, Word
from idp.domain.routing import Locality, PolicySnapshot, ProcessingMode, clamp_policy
from idp.domain.taxonomy import ClassificationRules, SchemaDefinition
from idp.providers.extraction.base import ExtractionContext
from idp.providers.extraction.llm import LLMExtractor, parse_answer
from idp.providers.llm.adapters import (
    AnthropicProvider,
    MockLLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from idp.providers.llm.base import LLMCallError, LLMRequest
from idp.providers.llm.classifier import LLMClassifier
from idp.providers.llm.gateway import CallRecord, LLMGateway

REQUEST = LLMRequest(system="sys", user="user", json_schema={"type": "object"})


class Recorder:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def ok(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body)


def ollama(recorder: Recorder, host: str = "ollama", name: str = "ollama") -> OllamaProvider:
    return OllamaProvider(
        name=name,
        base_url=f"http://{host}:11434",
        model="llama3.1:8b",
        declared_locality=Locality.LOCAL,
        timeout_seconds=5,
        client=recorder.client(),
    )


def anthropic(recorder: Recorder) -> AnthropicProvider:
    return AnthropicProvider(
        name="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-sonnet-5-5",
        declared_locality=Locality.CLOUD,
        timeout_seconds=5,
        api_key="test-key",
        cost_input_per_1k=0.003,
        cost_output_per_1k=0.015,
        client=recorder.client(),
    )


def gateway(*providers: Any, allowed: set[str] | None = None) -> LLMGateway:
    async def no_sleep(_: float) -> None:
        return None

    return LLMGateway(
        {p.name: p for p in providers},
        allowed_hosts=frozenset(allowed or {"ollama", "api.anthropic.com", "vllm.internal"}),
        local_hosts=frozenset({"ollama"}),
        sleep=no_sleep,
    )


async def test_openai_compatible_adapter() -> None:
    rec = Recorder(
        ok(
            {
                "model": "m",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            }
        )
    )
    provider = OpenAICompatibleProvider(
        name="openai-compatible",
        base_url="http://vllm.internal/v1",
        model="m",
        declared_locality=Locality.LOCAL,
        timeout_seconds=5,
        api_key="k",
        client=rec.client(),
    )
    response = await provider.complete(REQUEST)
    assert (response.text, response.usage.input_tokens, response.usage.output_tokens) == (
        "{}",
        10,
        3,
    )
    sent = rec.requests[0]
    assert str(sent.url) == "http://vllm.internal/v1/chat/completions"
    assert sent.headers["Authorization"] == "Bearer k"
    assert json.loads(sent.content)["response_format"] == {"type": "json_object"}


async def test_anthropic_and_ollama_adapters() -> None:
    rec = Recorder(
        ok(
            {
                "content": [{"type": "text", "text": '{"a": 1}'}],
                "usage": {"input_tokens": 7, "output_tokens": 2},
            }
        )
    )
    response = await anthropic(rec).complete(REQUEST)
    assert response.text == '{"a": 1}' and response.usage.input_tokens == 7
    assert rec.requests[0].headers["x-api-key"] == "test-key"
    assert rec.requests[0].headers["anthropic-version"] == "2023-06-01"

    rec = Recorder(ok({"message": {"content": "{}"}, "prompt_eval_count": 5, "eval_count": 1}))
    response = await ollama(rec).complete(REQUEST)
    assert response.usage.output_tokens == 1
    assert json.loads(rec.requests[0].content)["format"] == {"type": "object"}


@pytest.mark.parametrize(
    ("response", "code", "transient"),
    [
        (httpx.Response(503), "http_503", True),
        (httpx.Response(429), "http_429", True),
        (httpx.Response(401), "http_401", False),
        (httpx.Response(200, text="not json"), "invalid_json_response", False),
    ],
)
async def test_adapter_error_mapping(response: httpx.Response, code: str, transient: bool) -> None:
    with pytest.raises(LLMCallError) as caught:
        await ollama(Recorder(response)).complete(REQUEST)
    assert (caught.value.code, caught.value.transient) == (code, transient)


async def test_local_only_policy_refuses_cloud_without_any_request() -> None:
    rec = Recorder()
    gw = gateway(anthropic(rec))
    sink: list[CallRecord] = []
    with pytest.raises(PolicyViolationError):
        await gw.complete(
            "anthropic", REQUEST, policy=PolicySnapshot(), purpose="extraction", sink=sink
        )
    assert rec.requests == []
    assert sink[0].status == "refused" and sink[0].locality == "cloud"


async def test_locality_is_fail_safe_and_hosts_are_allow_listed() -> None:
    rec = Recorder()
    elsewhere = ollama(rec, host="vllm.internal", name="remote-ollama")
    gw = gateway(elsewhere)
    assert (
        gw.effective_locality("remote-ollama") is Locality.CLOUD
    )  # declared local, host not local
    with pytest.raises(PolicyViolationError):
        await gw.complete("remote-ollama", REQUEST, policy=PolicySnapshot(), purpose="x", sink=[])
    blocked = gateway(ollama(rec), allowed={"api.anthropic.com"})
    assert blocked.refusal("ollama", PolicySnapshot()) == "host ollama is not in LLM_ALLOWED_HOSTS"
    assert (
        gateway(MockLLMProvider()).refusal("mock-llm", PolicySnapshot()) == "mock provider disabled"
    )
    assert rec.requests == []


async def test_retries_transient_errors_and_meters_usage() -> None:
    rec = Recorder(
        httpx.Response(503),
        ok(
            {
                "content": [{"type": "text", "text": "{}"}],
                "usage": {"input_tokens": 1000, "output_tokens": 100},
            }
        ),
    )
    gw = gateway(anthropic(rec))
    sink: list[CallRecord] = []
    policy = PolicySnapshot(ProcessingMode.CLOUD_ALLOWED)
    await gw.complete("anthropic", REQUEST, policy=policy, purpose="extraction", sink=sink)
    [call] = sink
    assert (call.status, call.attempts, call.input_tokens) == ("ok", 2, 1000)
    assert call.cost == pytest.approx(0.003 + 0.0015)

    failing = gateway(anthropic(Recorder(*(httpx.Response(500) for _ in range(3)))))
    with pytest.raises(ProviderError):
        await failing.complete("anthropic", REQUEST, policy=policy, purpose="x", sink=sink)
    assert sink[-1].status == "failed" and sink[-1].attempts == 3


def test_tenant_policy_cannot_exceed_the_deployment_ceiling() -> None:
    requested = PolicySnapshot(
        ProcessingMode.CLOUD_ALLOWED, allow_mock_providers=True, max_cost_per_document=5, version=3
    )
    effective = clamp_policy(
        requested, PolicySnapshot(ProcessingMode.HYBRID, max_cost_per_document=1)
    )
    assert effective.mode is ProcessingMode.HYBRID
    assert effective.allow_mock_providers is False
    assert effective.max_cost_per_document == 1
    assert effective.version == 3


def _layout() -> PageLayout:
    def line(i: int, text: str, y: float) -> Line:
        return Line(id=f"p1-l{i}", words=(Word(text, BBox(0.1, y, 0.5, y + 0.02)),))

    return PageLayout(
        page_number=1,
        width=600,
        height=800,
        unit="pt",
        rotation=0,
        source=TextSource.NATIVE,
        blocks=(
            Block(
                lines=(
                    line(0, "ACME Industrial GmbH", 0.05),
                    line(1, "Invoice No INV-7", 0.1),
                    line(2, "Total 1,249.50", 0.5),
                )
            ),
        ),
    )


SCHEMA = SchemaDefinition.model_validate(
    {
        "fields": [
            {"name": "vendor_name", "type": "string", "required": True},
            {"name": "invoice_number", "type": "string"},
            {"name": "total", "type": "decimal"},
            {"name": "po_number", "type": "string"},
        ]
    }
)


async def test_llm_extractor_grounds_every_value() -> None:
    answer = {
        "fields": {
            "vendor_name": {"value": "ACME Industrial GmbH", "line": "p1-l0"},
            "invoice_number": {"value": "INV-7", "line": "p1-l2"},  # wrong citation
            "total": {"value": "1,249.50", "line": "p1-l2"},
            "po_number": {"value": "PO-999", "line": "p1-l1"},  # invented
            "unknown_field": {"value": "x", "line": None},
        },
        "rows": {},
    }
    rec = Recorder(
        ok({"message": {"content": json.dumps(answer)}, "prompt_eval_count": 50, "eval_count": 20})
    )
    gw = gateway(ollama(rec))
    extractor = LLMExtractor(gw, "ollama", max_input_chars=5000)
    assert extractor.info.tier.name == "LOCAL_LLM"
    ctx = ExtractionContext(schema=SCHEMA, layouts=(_layout(),))
    found = {c.path: c for c in await extractor.extract(ctx)}
    assert set(found) == {"vendor_name", "invoice_number", "total", "po_number"}
    assert found["vendor_name"].confidence == 0.8 and found["vendor_name"].bbox is not None
    assert found["invoice_number"].confidence == 0.72
    assert found["total"].value == "1249.50"
    assert found["po_number"].confidence == 0.3 and found["po_number"].bbox is None
    assert "not found in the document text" in (found["po_number"].reason or "")
    assert ctx.calls[0].input_tokens == 50
    prompt = json.loads(rec.requests[0].content)["messages"][1]["content"]
    assert "[p1-l2] Total 1,249.50" in prompt


def test_malformed_answers_are_provider_errors() -> None:
    with pytest.raises(ProviderError):
        parse_answer("Sure! Here is the data: vendor ACME")
    assert parse_answer('```json\n{"fields": {}, "rows": {}}\n```').fields == {}


async def test_classifier_only_accepts_known_keys() -> None:
    import uuid

    candidates = [
        TypeCandidate(
            uuid.uuid4(), "invoice", uuid.uuid4(), ClassificationRules(keywords=["invoice"])
        ),
        TypeCandidate(
            uuid.uuid4(), "receipt", uuid.uuid4(), ClassificationRules(keywords=["receipt"])
        ),
    ]
    rec = Recorder(
        ok({"message": {"content": '{"document_type": "receipt"}'}}),
        ok({"message": {"content": '{"document_type": "contract"}'}}),
    )
    classifier = LLMClassifier(gateway(ollama(rec)))
    sink: list[CallRecord] = []
    answer = await classifier.classify(
        "Thank you for shopping", candidates, policy=PolicySnapshot(), sink=sink
    )
    assert answer is not None and answer.candidate.key == "receipt" and answer.confidence == 0.6
    assert await classifier.classify("text", candidates, policy=PolicySnapshot(), sink=sink) is None
    assert (
        await classifier.classify(
            "text", candidates, policy=PolicySnapshot(allow_llm=False), sink=sink
        )
        is None
    )
    assert len(rec.requests) == 2
