"""Connections, master-data import, and enrichment inside the pipeline."""

import uuid

import httpx
import pytest

from idp.application.jobs import RunOutcome
from tests.fixtures import files
from tests.integration.conftest import TenantFixture, login, upload

CSV = (
    b"key;name;tax_id;iban;payment_terms\n"
    b"V-1001;ACME Industrial Supplies GmbH;DE 123 456 789;;NET30\n"
    b"V-1002;Globex Office Solutions Ltd;GB987654321;;NET45\n"
    b"V-1002;Duplicate Key Ltd;;;\n"
    b";Missing Key;;;\n"
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


async def _vendors(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    created = await client.post(
        "/api/v1/connections",
        headers=headers,
        json={"key": "vendors", "name": "Vendor master", "kind": "master_data"},
    )
    assert created.status_code == 201
    connection_id = created.json()["id"]
    imported = await client.post(
        f"/api/v1/connections/{connection_id}/records/import",
        headers=headers,
        files={"file": ("vendors.csv", CSV, "text/csv")},
    )
    assert imported.status_code == 200
    body = imported.json()
    assert (body["imported"], body["skipped"]) == (2, 2)
    assert any("duplicate key" in e for e in body["errors"])
    return str(connection_id)


async def _extraction(client: httpx.AsyncClient, headers: dict[str, str], make_runner, data: bytes):  # type: ignore[no-untyped-def]
    body = (await upload(client, headers, data)).json()
    outcome = await make_runner().run(uuid.UUID(body["job_id"]))
    result = (
        await client.get(f"/api/v1/documents/{body['document']['id']}/extraction", headers=headers)
    ).json()
    return outcome, result


async def test_master_data_import_search_and_test_lookup(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    connection_id = await _vendors(client, owner)
    page = (
        await client.get(f"/api/v1/connections/{connection_id}/records?q=acme", headers=owner)
    ).json()
    assert page["total"] == 1
    assert page["items"][0]["attributes"] == {"tax_id": "DE 123 456 789", "payment_terms": "NET30"}
    listed = (await client.get("/api/v1/connections", headers=owner)).json()
    assert listed[0]["records"] == 2

    lookup = await client.post(
        f"/api/v1/connections/{connection_id}/test",
        headers=owner,
        json={"criteria": {"tax_id": "DE123456789"}},
    )
    assert lookup.json()["status"] == "matched"
    assert lookup.json()["best"]["key"] == "V-1001"

    duplicate = await client.post(
        "/api/v1/connections",
        headers=owner,
        json={"key": "vendors", "name": "x", "kind": "master_data"},
    )
    assert duplicate.status_code == 409


async def test_invoice_vendor_is_enriched_from_master_data(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner
) -> None:  # type: ignore[no-untyped-def]
    await _vendors(client, owner)
    outcome, result = await _extraction(client, owner, make_runner, files.clean_invoice_pdf())
    assert outcome is RunOutcome.SUCCEEDED
    [vendor] = result["parts"][0]["enrichment"]
    assert vendor["status"] == "matched"
    assert vendor["record_key"] == "V-1001"
    assert set(vendor["matched_on"]) == {"name", "tax_id"}
    assert vendor["outputs"]["payment_terms"] == "NET30"
    assert vendor["is_mock"] is False


async def test_lookup_rule_sends_unknown_vendors_to_review(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner
) -> None:  # type: ignore[no-untyped-def]
    # Require a known vendor: add a lookup rule to the invoice schema.
    [doc_type] = (await client.get("/api/v1/document-types", headers=owner)).json()
    published = (
        await client.get(f"/api/v1/document-types/{doc_type['id']}/schemas/1", headers=owner)
    ).json()["definition"]
    published["rules"].append(
        {
            "type": "lookup",
            "params": {"enrichment": "vendor"},
            "severity": "REQUIRES_HUMAN",
            "message": None,
        }
    )
    assert (
        await client.put(
            f"/api/v1/document-types/{doc_type['id']}/draft",
            headers=owner,
            json={"definition": published},
        )
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/document-types/{doc_type['id']}/publish", headers=owner)
    ).status_code == 200

    # No `vendors` connection yet: honest "not configured", and review is required.
    outcome, result = await _extraction(client, owner, make_runner, files.clean_invoice_pdf())
    assert outcome is RunOutcome.WAITING_FOR_REVIEW
    assert result["parts"][0]["enrichment"][0]["status"] == "not_configured"
    lookup = [v for v in result["parts"][0]["validation"] if v["rule_type"] == "lookup"]
    assert lookup[0]["outcome"] == "REQUIRES_HUMAN"
    assert lookup[0]["message"] == "Lookup 'vendor' returned not configured"

    # With master data the same invoice passes.
    await _vendors(client, owner)
    outcome, result = await _extraction(
        client, owner, make_runner, files.clean_invoice_pdf(total="1,249.50  EUR")
    )
    assert outcome is RunOutcome.SUCCEEDED


async def test_mock_erp_is_refused_unless_mock_providers_are_allowed(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner
) -> None:  # type: ignore[no-untyped-def]
    created = await client.post(
        "/api/v1/connections",
        headers=owner,
        json={"key": "vendors", "name": "Demo ERP", "kind": "mock_erp"},
    )
    assert created.json()["is_mock"] is True
    _, result = await _extraction(client, owner, make_runner, files.clean_invoice_pdf())
    [vendor] = result["parts"][0]["enrichment"]
    assert (vendor["status"], vendor["message"]) == (
        "not_configured",
        "mock provider disabled by policy",
    )


async def test_rest_connections_need_an_allow_listed_host(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/connections",
        headers=owner,
        json={
            "key": "erp",
            "name": "ERP",
            "kind": "rest",
            "config": {"base_url": "https://erp.example.com", "query": {"tax_id": "vat"}},
        },
    )
    assert response.status_code == 422
    assert "ENRICHMENT_ALLOWED_HOSTS" in response.json()["detail"]
    bad = await client.post(
        "/api/v1/connections",
        headers=owner,
        json={
            "key": "erp",
            "name": "ERP",
            "kind": "rest",
            "config": {"base_url": "ftp://x", "query": {}},
        },
    )
    assert bad.status_code == 422
