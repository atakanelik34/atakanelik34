"""Routing inside the extract step: route trace, fallback stages, policy, failures."""

import uuid
from dataclasses import dataclass, field

import httpx
import pytest

from idp.application.jobs import RunOutcome
from idp.application.policy import StaticPolicyResolver
from idp.application.steps.extract import ExtractStep
from idp.container import Container
from idp.domain.routing import Locality, PolicySnapshot, ProcessingMode, Tier
from idp.providers.extraction.base import (
    SCALAR_ROW,
    ExtractionContext,
    FieldCandidate,
    ProviderInfo,
    Suitability,
)
from idp.providers.extraction.key_value import KeyValueExtractor
from idp.providers.extraction.regex_extractor import RegexExtractor
from idp.providers.extraction.table import TableExtractor
from tests.fixtures import files
from tests.integration.conftest import TenantFixture, login, upload


@dataclass
class StubProvider:
    """Test double standing in for a later-tier provider (e.g. an LLM)."""

    info: ProviderInfo
    value: str = "ACME Industrial GmbH"
    fail: bool = False
    calls: list[int] = field(default_factory=list)

    def assess(self, ctx: ExtractionContext) -> Suitability:
        return Suitability(True, 0.9, self.info.cost_per_page, ("stub",))

    async def extract(self, ctx: ExtractionContext) -> list[FieldCandidate]:
        self.calls.append(1)
        if self.fail:
            raise RuntimeError("provider down")
        return [
            FieldCandidate(
                path="vendor_name",
                row_id=SCALAR_ROW,
                raw_text=self.value,
                value=self.value,
                normalized=True,
                confidence=0.95,
                page=1,
                bbox=None,
                method="llm",
                provider=self.info.name,
                provider_version="1",
            )
        ]


def _step(
    container: Container, *extra: StubProvider, policy: PolicySnapshot | None = None
) -> ExtractStep:
    return ExtractStep(
        storage=container.storage,
        providers=[RegexExtractor(), KeyValueExtractor(), TableExtractor(), *extra],
        policy=StaticPolicyResolver(policy or PolicySnapshot()),
    )


@pytest.fixture
async def owner(client: httpx.AsyncClient, acme: TenantFixture, pin_workflow) -> dict[str, str]:  # type: ignore[no-untyped-def]
    pin_workflow(4)  # stop after extraction
    headers = await login(client, acme.owner_email, acme.owner_password)
    response = await client.post(
        "/api/v1/document-types/from-template",
        headers=headers,
        json={"template_key": "invoice", "publish": True},
    )
    assert response.status_code == 201
    return headers


async def _run(client, headers, make_runner, step: ExtractStep, expected=RunOutcome.SUCCEEDED):  # type: ignore[no-untyped-def]
    body = (await upload(client, headers, files.invoice_pdf())).json()
    outcome = await make_runner({"extract": step}).run(uuid.UUID(body["job_id"]))
    assert outcome is expected
    doc_id = body["document"]["id"]
    return (await client.get(f"/api/v1/documents/{doc_id}/extraction", headers=headers)).json()


async def test_route_trace_explains_the_deterministic_route(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner, container: Container
) -> None:  # type: ignore[no-untyped-def]
    result = await _run(client, owner, make_runner, _step(container))
    trace = result["parts"][0]["extraction"]["route_trace"]
    assert result["parts"][0]["extraction"]["route"] == "NATIVE_TEXT"
    assert trace["signals"]["native_pages"] == 1
    assert trace["planned_stages"] == [
        ["regex-extractor", "key-value-extractor", "table-extractor"]
    ]
    [stage] = trace["stages"]
    assert {a["status"] for a in stage["attempts"]} == {"ok"}
    # The letterhead vendor heuristic is below threshold and nothing can escalate.
    assert stage["unresolved"] == ["vendor_name"]
    assert stage["outcome"] == "exhausted"


async def test_unresolved_required_field_escalates_to_the_next_stage(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner, container: Container
) -> None:  # type: ignore[no-untyped-def]
    fallback = StubProvider(ProviderInfo("local-llm-stub", "1", "llm", tier=Tier.LOCAL_LLM))
    result = await _run(client, owner, make_runner, _step(container, fallback))
    part = result["parts"][0]
    stages = part["extraction"]["route_trace"]["stages"]
    assert [s["outcome"] for s in stages] == ["escalated", "accepted"]
    assert part["fields"]["vendor_name"]["value"] == "ACME Industrial GmbH"
    assert part["fields"]["vendor_name"]["provider"] == "local-llm-stub"
    assert "local-llm-stub" in part["extraction"]["providers"]
    assert fallback.calls == [1]


async def test_local_only_policy_never_calls_a_cloud_provider(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner, container: Container
) -> None:  # type: ignore[no-untyped-def]
    cloud = StubProvider(
        ProviderInfo(
            "cloud-stub",
            "1",
            "llm",
            locality=Locality.CLOUD,
            tier=Tier.CLOUD_LLM,
            cost_per_page=0.02,
        )
    )
    result = await _run(client, owner, make_runner, _step(container, cloud))
    trace = result["parts"][0]["extraction"]["route_trace"]
    assert cloud.calls == []
    assert {
        "provider": "cloud-stub",
        "reason": "policy LOCAL_ONLY forbids cloud providers",
    } in trace["rejected"]

    # Same document, reprocessed under a HYBRID policy: cloud becomes a fallback.
    doc_id = result["document_id"]
    replay = await client.post(f"/api/v1/documents/{doc_id}/process", headers=owner)
    step = _step(container, cloud, policy=PolicySnapshot(ProcessingMode.HYBRID))
    assert await make_runner({"extract": step}).run(uuid.UUID(replay.json()["id"])) is (
        RunOutcome.SUCCEEDED
    )
    hybrid = (await client.get(f"/api/v1/documents/{doc_id}/extraction", headers=owner)).json()
    assert cloud.calls == [1]
    assert hybrid["parts"][0]["extraction"]["route_trace"]["estimated_cost"] == 0.02
    assert hybrid["metrics"]["estimated_cost"] == 0.02


async def test_provider_failures_are_isolated_and_total_failure_retries(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner, container: Container
) -> None:  # type: ignore[no-untyped-def]
    broken = StubProvider(ProviderInfo("broken-stub", "1", "llm", tier=Tier.LOCAL_LLM), fail=True)
    result = await _run(client, owner, make_runner, _step(container, broken))
    stages = result["parts"][0]["extraction"]["route_trace"]["stages"]
    assert stages[1]["attempts"] == [
        {
            "provider": "broken-stub",
            "status": "failed",
            "error": "RuntimeError",
            "duration_ms": stages[1]["attempts"][0]["duration_ms"],
        }
    ]
    assert stages[1]["outcome"] == "exhausted"

    only_broken = ExtractStep(
        storage=container.storage,
        providers=[StubProvider(ProviderInfo("broken-0", "1", "llm"), fail=True)],
        policy=StaticPolicyResolver(PolicySnapshot()),
    )
    body = (await upload(client, owner, files.clean_invoice_pdf())).json()
    outcome = await make_runner({"extract": only_broken}).run(uuid.UUID(body["job_id"]))
    assert outcome is RunOutcome.RETRY_SCHEDULED
