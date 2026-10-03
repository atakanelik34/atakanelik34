import uuid

import httpx

from idp.application.jobs import RunOutcome
from tests.fixtures import files
from tests.integration.conftest import TenantFixture, login, upload


async def test_provider_catalog_states_policy_and_unconfigured_providers(
    client: httpx.AsyncClient, acme: TenantFixture
) -> None:
    headers = await login(client, acme.owner_email, acme.owner_password)
    body = (await client.get("/api/v1/providers", headers=headers)).json()
    assert body["policy"]["mode"] == "LOCAL_ONLY"
    assert body["policy"]["allow_mock_providers"] is False
    by_name = {p["name"]: p for p in body["providers"]}
    assert by_name["regex-extractor"]["tier"] == 0
    assert by_name["table-extractor"]["status"] == "configured"
    ocr = next(p for p in body["providers"] if p["kind"] == "ocr")
    assert ocr["status"] in {"configured", "not_configured"}
    assert all(p["locality"] == "local" for p in body["providers"] if p["status"] == "configured")
    llms = {p["name"]: p["status"] for p in body["providers"] if p["kind"] == "llm"}
    assert llms == {
        "ollama": "not_configured",
        "openai-compatible": "not_configured",
        "anthropic": "not_configured",
        "mock-llm": "not_configured",
    }


async def test_workflows_and_job_monitor(
    client: httpx.AsyncClient, acme: TenantFixture, make_runner
) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    workflows = (await client.get("/api/v1/workflows", headers=headers)).json()
    default = [w for w in workflows if w["is_default"]]
    assert len(default) == 1
    assert default[0]["steps"][-2:] == ["validate", "review"]

    body = (await upload(client, headers, files.native_pdf())).json()
    jobs = (await client.get("/api/v1/jobs?status=QUEUED", headers=headers)).json()
    assert [j["id"] for j in jobs] == [body["job_id"]]
    assert jobs[0]["document_name"] == "invoice.pdf"
    outcome = await make_runner().run(uuid.UUID(body["job_id"]))
    assert outcome in {RunOutcome.SUCCEEDED, RunOutcome.WAITING_FOR_REVIEW}
    assert (await client.get("/api/v1/jobs?status=QUEUED", headers=headers)).json() == []
    assert len((await client.get("/api/v1/jobs", headers=headers)).json()) == 1
