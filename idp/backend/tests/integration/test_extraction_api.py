import uuid

import httpx
import pytest

from idp.application.jobs import RunOutcome
from tests.fixtures import files
from tests.integration.conftest import TenantFixture, login, upload


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


async def _process(client, headers, data, make_runner) -> str:  # type: ignore[no-untyped-def]
    body = (await upload(client, headers, data)).json()
    assert await make_runner().run(uuid.UUID(body["job_id"])) is RunOutcome.SUCCEEDED
    return str(body["document"]["id"])


async def test_extraction_result_contract(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner
) -> None:  # type: ignore[no-untyped-def]
    doc_id = await _process(client, owner, files.invoice_pdf(), make_runner)
    result = (await client.get(f"/api/v1/documents/{doc_id}/extraction", headers=owner)).json()

    assert result["document_id"] == doc_id
    [part] = result["parts"]
    assert part["classification"]["document_type"] == "invoice"
    assert part["schema_version"] == 1
    assert part["extraction"]["providers"] == [
        "regex-extractor",
        "key-value-extractor",
        "table-extractor",
    ]

    fields = part["fields"]
    assert fields["invoice_number"]["value"] == "INV-2026-00123"
    assert fields["total"]["value"] == "1249.50"
    assert fields["total"]["threshold"] == 0.85
    assert fields["total"]["required"] is True
    prov = fields["total"]["provenance"]
    assert prov["page"] == 1
    assert len(prov["bbox"]) == 4
    assert prov["source_text"] == "1,249.50"
    assert prov["method"].startswith("key_value")
    assert prov["schema_version_id"]
    assert prov["pipeline_version"]
    # Heuristic vendor name is flagged for attention, not presented as certain.
    assert fields["vendor_name"]["below_threshold"] is True

    rows = part["tables"]["lines[]"]
    assert len(rows) == 3
    assert {r["cells"]["total"]["value"] for r in rows} == {"900.00", "125.00", "25.00"}
    assert all(r["row_id"].startswith("r_") for r in rows)
    assert part["validation"] == []
    assert result["metrics"]["estimated_cost"] == 0.0


async def test_reprocessing_keeps_previous_results(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner
) -> None:  # type: ignore[no-untyped-def]
    doc_id = await _process(client, owner, files.invoice_pdf(), make_runner)
    first = (await client.get(f"/api/v1/documents/{doc_id}/extraction", headers=owner)).json()
    replay = await client.post(f"/api/v1/documents/{doc_id}/process", headers=owner)
    assert await make_runner().run(uuid.UUID(replay.json()["id"])) is RunOutcome.SUCCEEDED
    second = (await client.get(f"/api/v1/documents/{doc_id}/extraction", headers=owner)).json()
    assert second["job_id"] != first["job_id"]
    old_parts = (
        await client.get(
            f"/api/v1/documents/{doc_id}/parts?job_id={first['job_id']}", headers=owner
        )
    ).json()
    assert old_parts[0]["job_id"] == first["job_id"]


async def test_unclassified_documents_have_no_fields(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner
) -> None:  # type: ignore[no-untyped-def]
    doc_id = await _process(
        client,
        owner,
        files.make_pdf(["A personal letter about gardening and weather"]),
        make_runner,
    )
    result = (await client.get(f"/api/v1/documents/{doc_id}/extraction", headers=owner)).json()
    [part] = result["parts"]
    assert part["classification"]["document_type"] is None
    assert part["fields"] == {}
    assert part["extraction"] is None


@pytest.fixture(autouse=True)
def _workflow(pin_workflow) -> None:  # type: ignore[no-untyped-def]
    # These tests exercise stages before validation/review (workflow ingest v4).
    pin_workflow(4)
