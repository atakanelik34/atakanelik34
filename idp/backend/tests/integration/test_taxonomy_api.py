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
    return await login(client, acme.owner_email, acme.owner_password)


async def _from_template(
    client: httpx.AsyncClient, headers: dict[str, str], key: str, publish: bool = True
) -> dict:
    response = await client.post(
        "/api/v1/document-types/from-template",
        headers=headers,
        json={"template_key": key, "publish": publish},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_templates_and_publish_lifecycle(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    templates = (await client.get("/api/v1/document-type-templates", headers=owner)).json()
    assert {t["key"] for t in templates} >= {"invoice", "delivery_note", "contract"}

    created = await _from_template(client, owner, "invoice", publish=False)
    type_id = created["id"]
    assert [(v["version"], v["status"]) for v in created["versions"]] == [(1, "draft")]

    v1 = (await client.post(f"/api/v1/document-types/{type_id}/publish", headers=owner)).json()
    assert (v1["version"], v1["status"]) == (1, "published")
    assert (
        await client.post(f"/api/v1/document-types/{type_id}/publish", headers=owner)
    ).status_code == 409

    # Start a new draft from the published version and change it.
    draft = (
        await client.put(f"/api/v1/document-types/{type_id}/draft", headers=owner, json={})
    ).json()
    assert (draft["version"], draft["status"]) == (2, "draft")
    definition = draft["definition"]
    definition["fields"].append(
        {"name": "cost_center", "type": "string", "aliases": ["cost center"]}
    )
    saved = await client.put(
        f"/api/v1/document-types/{type_id}/draft", headers=owner, json={"definition": definition}
    )
    assert saved.status_code == 200
    v2 = (await client.post(f"/api/v1/document-types/{type_id}/publish", headers=owner)).json()
    assert v2["version"] == 2

    detail = (await client.get(f"/api/v1/document-types/{type_id}", headers=owner)).json()
    assert [(v["version"], v["status"]) for v in detail["versions"]] == [
        (2, "published"),
        (1, "retired"),
    ]
    # History is immutable and still readable.
    old = (await client.get(f"/api/v1/document-types/{type_id}/schemas/1", headers=owner)).json()
    assert "cost_center" not in [f["name"] for f in old["definition"]["fields"]]

    listed = (await client.get("/api/v1/document-types", headers=owner)).json()
    assert listed[0]["published_version"] == 2
    assert listed[0]["draft_version"] is None


async def test_invalid_definition_returns_field_errors(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/document-types",
        headers=owner,
        json={
            "key": "custom",
            "name": "Custom",
            "definition": {"fields": [{"name": "lines", "type": "array"}]},
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "Schema definition is invalid"
    assert body["details"]["errors"][0]["loc"][0] == "fields"
    duplicate = await client.post(
        "/api/v1/document-types", headers=owner, json={"key": "custom", "name": "C"}
    )
    assert duplicate.status_code == 201
    again = await client.post(
        "/api/v1/document-types", headers=owner, json={"key": "custom", "name": "C"}
    )
    assert again.status_code == 409
    bad_key = await client.post(
        "/api/v1/document-types", headers=owner, json={"key": "Bad Key", "name": "C"}
    )
    assert bad_key.status_code == 422


async def test_taxonomy_permissions_and_isolation(
    client: httpx.AsyncClient, container: Container, acme: TenantFixture, owner: dict[str, str]
) -> None:
    created = await _from_template(client, owner, "receipt")
    await add_user(container, acme.owner_email, "rev@acme.test", Role.REVIEWER)
    reviewer = await login(client, "rev@acme.test", USER_PASSWORD)
    assert (await client.get("/api/v1/document-types", headers=reviewer)).status_code == 200
    denied = await client.post(f"/api/v1/document-types/{created['id']}/publish", headers=reviewer)
    assert denied.status_code == 403

    globex = await bootstrap_tenant(container, "globex")
    other = await login(client, globex.owner_email, globex.owner_password)
    assert (
        await client.get(f"/api/v1/document-types/{created['id']}", headers=other)
    ).status_code == 404
    assert (await client.get("/api/v1/document-types", headers=other)).json() == []


async def test_mixed_packet_is_classified_and_split(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    make_runner,  # type: ignore[no-untyped-def]
) -> None:
    for key in ("invoice", "delivery_note", "contract", "purchase_order"):
        await _from_template(client, owner, key)
    body = (await upload(client, owner, files.mixed_packet())).json()
    assert await make_runner().run(uuid.UUID(body["job_id"])) is RunOutcome.SUCCEEDED

    parts = (
        await client.get(f"/api/v1/documents/{body['document']['id']}/parts", headers=owner)
    ).json()
    assert [(p["page_start"], p["page_end"], p["document_type_key"]) for p in parts] == [
        (1, 3, "invoice"),
        (4, 5, "delivery_note"),
        (6, 10, "contract"),
    ]
    assert all(p["schema_version"] == 1 for p in parts)
    assert parts[0]["classifier"] == "rule-classifier@1"


async def test_without_taxonomy_parts_are_unclassified(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    make_runner,  # type: ignore[no-untyped-def]
) -> None:
    body = (await upload(client, owner, files.native_pdf(2))).json()
    assert await make_runner().run(uuid.UUID(body["job_id"])) is RunOutcome.SUCCEEDED
    parts = (
        await client.get(f"/api/v1/documents/{body['document']['id']}/parts", headers=owner)
    ).json()
    assert [(p["page_start"], p["page_end"], p["status"]) for p in parts] == [
        (1, 2, "unclassified")
    ]
