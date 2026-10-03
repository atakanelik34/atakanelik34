"""Approved reviews become ground truth; runs score the machine output against it."""

import uuid

import httpx
import pytest

from idp.application.jobs import RunOutcome
from idp.container import Container
from idp.domain.identity import Role
from tests.fixtures import files
from tests.integration.conftest import (
    USER_PASSWORD,
    TenantFixture,
    add_user,
    bootstrap_tenant,
    login,
    upload,
)


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


async def _reviewed_invoice(client: httpx.AsyncClient, headers: dict[str, str], make_runner) -> str:  # type: ignore[no-untyped-def]
    body = (await upload(client, headers, files.invoice_pdf())).json()
    job_id = uuid.UUID(body["job_id"])
    assert await make_runner().run(job_id) is RunOutcome.WAITING_FOR_REVIEW
    [task] = (await client.get("/api/v1/reviews", headers=headers)).json()
    detail = (await client.get(f"/api/v1/reviews/{task['id']}", headers=headers)).json()
    fields = detail["parts"][0]["fields"]
    for name, value in (
        ("vendor_name", "ACME Industrial Supplies GmbH"),
        ("invoice_number", "INV-2026-00999"),
    ):
        response = await client.post(
            f"/api/v1/reviews/{task['id']}/fields/{fields[name]['id']}",
            headers=headers,
            json={"action": "edit", "value": value},
        )
        assert response.status_code == 204
    assert (
        await client.post(f"/api/v1/reviews/{task['id']}/approve", headers=headers, json={})
    ).status_code == 204
    assert await make_runner().run(job_id) is RunOutcome.SUCCEEDED
    return str(body["document"]["id"])


async def test_dataset_from_reviews_and_run_metrics(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner
) -> None:  # type: ignore[no-untyped-def]
    doc_id = await _reviewed_invoice(client, owner, make_runner)

    created = await client.post(
        "/api/v1/evaluation/datasets",
        headers=owner,
        json={"name": "invoices-golden", "document_type": "invoice"},
    )
    assert created.status_code == 201
    dataset = created.json()
    assert (dataset["items"], dataset["document_type"], dataset["last_run"]) == (0, "invoice", None)
    duplicate = await client.post(
        "/api/v1/evaluation/datasets", headers=owner, json={"name": "invoices-golden"}
    )
    assert duplicate.status_code == 409

    url = f"/api/v1/evaluation/datasets/{dataset['id']}"
    assert (await client.post(f"{url}/import-reviews", headers=owner)).json() == {"added": 1}
    assert (await client.post(f"{url}/import-reviews", headers=owner)).json() == {"added": 0}

    run = await client.post(f"{url}/runs", headers=owner)
    assert run.status_code == 201
    body = run.json()
    metrics = body["metrics"]
    assert metrics["items"] == 1
    assert metrics["intervention_rate"] == 1.0
    assert metrics["fn"] >= 1  # invoice number was corrected by the reviewer
    assert body["field_metrics"]["invoice_number"]["tp"] == 0
    assert body["field_metrics"]["total"]["tp"] == 1
    assert body["field_metrics"]["vendor_name"]["tp"] == 1  # machine was right, only unsure
    assert body["field_metrics"]["lines[].total"]["tp"] == 3
    [item] = body["item_results"]
    assert item["document_id"] == doc_id
    assert "invoice_number" in item["wrong_fields"]
    assert body["config"]["route"] == ["NATIVE_TEXT"]
    assert body["config"]["workflow"] == ["ingest@v5"]

    listed = (await client.get("/api/v1/evaluation/datasets", headers=owner)).json()
    assert listed[0]["items"] == 1 and listed[0]["last_run"]["id"] == body["id"]
    assert (
        await client.get(f"/api/v1/evaluation/runs/{body['id']}", headers=owner)
    ).status_code == 200

    audit = (
        await client.get("/api/v1/audit-logs?action=evaluation.run_completed", headers=owner)
    ).json()
    assert len(audit["items"]) == 1


async def test_evaluation_permissions_and_isolation(
    client: httpx.AsyncClient, owner: dict[str, str], acme: TenantFixture, container: Container
) -> None:
    dataset = (
        await client.post("/api/v1/evaluation/datasets", headers=owner, json={"name": "d"})
    ).json()
    empty = await client.post(f"/api/v1/evaluation/datasets/{dataset['id']}/runs", headers=owner)
    assert empty.status_code == 422

    await add_user(container, acme.owner_email, "viewer@acme.test", Role.VIEWER)
    viewer_headers = await login(client, "viewer@acme.test", USER_PASSWORD)
    assert (
        await client.get("/api/v1/evaluation/datasets", headers=viewer_headers)
    ).status_code == 200
    forbidden = await client.post(
        "/api/v1/evaluation/datasets", headers=viewer_headers, json={"name": "x"}
    )
    assert forbidden.status_code == 403

    globex = await bootstrap_tenant(container, "globex")
    other = await login(client, globex.owner_email, globex.owner_password)
    assert (
        await client.get(f"/api/v1/evaluation/datasets/{dataset['id']}", headers=other)
    ).status_code == 404
