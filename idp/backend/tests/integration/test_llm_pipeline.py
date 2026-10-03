"""LLM providers inside the pipeline: routed fallback, accounting, policy, classification."""

import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from idp.application.jobs import RunOutcome
from idp.application.policy import DbPolicyResolver
from idp.application.steps.classify import ClassifyStep
from idp.application.steps.extract import ExtractStep
from idp.container import Container
from idp.domain.identity import Role
from idp.domain.routing import Locality, PolicySnapshot, ProcessingMode
from idp.infrastructure.db.models import DocumentPart, ProviderCall
from idp.providers.extraction.key_value import KeyValueExtractor
from idp.providers.extraction.llm import LLMExtractor
from idp.providers.extraction.regex_extractor import RegexExtractor
from idp.providers.extraction.table import TableExtractor
from idp.providers.llm.adapters import AnthropicProvider, OllamaProvider
from idp.providers.llm.classifier import LLMClassifier
from idp.providers.llm.gateway import LLMGateway
from tests.fixtures import files
from tests.integration.conftest import USER_PASSWORD, TenantFixture, add_user, login, upload


class FakeModelServer:
    """Stands in for the model server over HTTP (MockTransport); records every request."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        anthropic = "system" in body  # Messages API: system prompt at top level
        system = body["system"] if anthropic else body["messages"][0]["content"]
        if "You classify" in system:
            content = {"document_type": "invoice"}
        else:
            content = {
                "fields": {
                    "vendor_name": {"value": "ACME Industrial Supplies GmbH", "line": "p1-l0"}
                },
                "rows": {},
            }
        if anthropic:
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": json.dumps(content)}],
                    "usage": {"input_tokens": 420, "output_tokens": 30},
                },
            )
        return httpx.Response(
            200,
            json={
                "message": {"content": json.dumps(content)},
                "prompt_eval_count": 420,
                "eval_count": 30,
            },
        )


def _gateway(server: FakeModelServer, *, cloud: bool = False) -> LLMGateway:
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    provider = (
        AnthropicProvider(
            name="anthropic",
            base_url="https://api.anthropic.com",
            model="claude-sonnet-5-5",
            declared_locality=Locality.CLOUD,
            timeout_seconds=5,
            api_key="k",
            client=client,
        )
        if cloud
        else OllamaProvider(
            name="ollama",
            base_url="http://ollama:11434",
            model="llama3.1:8b",
            declared_locality=Locality.LOCAL,
            timeout_seconds=5,
            client=client,
        )
    )
    return LLMGateway(
        {provider.name: provider},
        allowed_hosts=frozenset({"ollama", "api.anthropic.com"}),
        local_hosts=frozenset({"ollama"}),
    )


def _steps(
    container: Container, gateway: LLMGateway, ceiling: PolicySnapshot | None = None
) -> dict:
    policy = DbPolicyResolver(ceiling or PolicySnapshot())
    name = gateway.provider_names[0]
    return {
        "classify": ClassifyStep(
            storage=container.storage, llm=LLMClassifier(gateway), policy=policy
        ),
        "extract": ExtractStep(
            storage=container.storage,
            providers=[
                RegexExtractor(),
                KeyValueExtractor(),
                TableExtractor(),
                LLMExtractor(gateway, name, max_input_chars=10_000),
            ],
            policy=policy,
        ),
    }


@pytest.fixture
async def owner(client: httpx.AsyncClient, acme: TenantFixture) -> dict[str, str]:
    headers = await login(client, acme.owner_email, acme.owner_password)
    response = await client.post(
        "/api/v1/document-types/from-template",
        headers=headers,
        json={"template_key": "invoice", "publish": True},
    )
    assert response.status_code == 201
    return headers


async def test_local_llm_resolves_low_confidence_field_and_is_metered(
    client: httpx.AsyncClient, owner: dict[str, str], container: Container, make_runner
) -> None:  # type: ignore[no-untyped-def]
    server = FakeModelServer()
    body = (await upload(client, owner, files.invoice_pdf())).json()
    outcome = await make_runner(_steps(container, _gateway(server))).run(uuid.UUID(body["job_id"]))
    assert outcome is RunOutcome.SUCCEEDED  # the vendor no longer needs a human
    doc_id = body["document"]["id"]
    result = (await client.get(f"/api/v1/documents/{doc_id}/extraction", headers=owner)).json()
    part = result["parts"][0]
    vendor = part["fields"]["vendor_name"]
    assert (vendor["provider"], vendor["confidence"]) == ("llm:ollama", 0.8)
    assert vendor["provenance"]["bbox"] is not None  # grounded in the letterhead line
    stages = part["extraction"]["route_trace"]["stages"]
    assert [s["outcome"] for s in stages] == ["escalated", "accepted"]
    # Document text went only to the local model; the prompt marks it as untrusted.
    [request] = server.requests
    assert "untrusted" in request["messages"][0]["content"]

    async with container.session_factory() as session:
        [call] = (await session.scalars(select(ProviderCall))).all()
    assert (call.provider, call.locality, call.purpose, call.status) == (
        "ollama",
        "local",
        "extraction",
        "ok",
    )
    assert (call.input_tokens, call.output_tokens) == (420, 30)
    usage = (await client.get("/api/v1/providers/usage", headers=owner)).json()
    assert usage[0]["calls"] == 1 and usage[0]["input_tokens"] == 420


async def test_cloud_llm_is_never_called_under_local_only_even_if_tenant_asks(
    client: httpx.AsyncClient, owner: dict[str, str], container: Container, make_runner
) -> None:  # type: ignore[no-untyped-def]
    # The tenant asks for CLOUD_ALLOWED; the deployment ceiling is LOCAL_ONLY.
    put = await client.put(
        "/api/v1/processing-policy", headers=owner, json={"mode": "CLOUD_ALLOWED"}
    )
    assert put.status_code == 200
    assert put.json()["requested"]["mode"] == "CLOUD_ALLOWED"
    assert put.json()["effective"]["mode"] == "LOCAL_ONLY"

    server = FakeModelServer()
    body = (await upload(client, owner, files.invoice_pdf())).json()
    steps = _steps(container, _gateway(server, cloud=True))
    assert await make_runner(steps).run(uuid.UUID(body["job_id"])) is RunOutcome.WAITING_FOR_REVIEW
    assert server.requests == []
    result = (
        await client.get(f"/api/v1/documents/{body['document']['id']}/extraction", headers=owner)
    ).json()
    rejected = result["parts"][0]["extraction"]["route_trace"]["rejected"]
    assert {
        "provider": "llm:anthropic",
        "reason": "policy LOCAL_ONLY forbids cloud providers",
    } in rejected

    # Under a permissive ceiling, the same tenant policy lets the cloud fallback run:
    # send the document back and reprocess it.
    [task] = (await client.get("/api/v1/reviews", headers=owner)).json()
    sent = await client.post(
        f"/api/v1/reviews/{task['id']}/send-back",
        headers=owner,
        json={"reason": "retry with cloud"},
    )
    steps = _steps(
        container, _gateway(server, cloud=True), PolicySnapshot(ProcessingMode.CLOUD_ALLOWED)
    )
    assert await make_runner(steps).run(uuid.UUID(sent.json()["job_id"])) is RunOutcome.SUCCEEDED
    assert len(server.requests) == 1  # escalated to the cloud model once


async def test_llm_classifies_what_rules_cannot(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner,
    pin_workflow,
) -> None:  # type: ignore[no-untyped-def]
    pin_workflow(3)  # stop after classification
    server = FakeModelServer()
    pdf = files.make_text_pdf(
        [[(72, "Statement of services rendered")], [(72, "Amount payable 120.00")]]
    )
    body = (await upload(client, owner, pdf)).json()
    steps = _steps(container, _gateway(server))
    assert await make_runner(steps).run(uuid.UUID(body["job_id"])) is RunOutcome.SUCCEEDED
    async with container.session_factory() as session:
        [part] = (await session.scalars(select(DocumentPart))).all()
    assert part.classifier == "llm:ollama"
    assert part.classification_confidence == 0.6
    assert part.status == "classified"


async def test_policy_api_permissions_and_audit(
    client: httpx.AsyncClient, owner: dict[str, str], acme: TenantFixture, container: Container
) -> None:
    state = (await client.get("/api/v1/processing-policy", headers=owner)).json()
    assert state["effective"]["mode"] == "LOCAL_ONLY" and state["effective"]["version"] == 0
    await add_user(container, acme.owner_email, "admin@acme.test", Role.ADMIN)
    admin = await login(client, "admin@acme.test", USER_PASSWORD)
    denied = await client.put("/api/v1/processing-policy", headers=admin, json={"mode": "HYBRID"})
    assert denied.status_code == 403
    updated = await client.put(
        "/api/v1/processing-policy", headers=owner, json={"mode": "LOCAL_ONLY", "allow_llm": False}
    )
    assert updated.json()["effective"]["allow_llm"] is False
    assert updated.json()["effective"]["version"] == 1
    audit = (await client.get("/api/v1/audit-logs?action=policy.updated", headers=owner)).json()
    assert audit["items"][0]["after"]["allow_llm"] is False
