"""Business actions (approval, execution, idempotency), outbox relay, API keys."""

import hashlib
import hmac
import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from idp.application.jobs import RunOutcome
from idp.application.outbox import OutboxRelay
from idp.application.policy import StaticPolicyResolver
from idp.application.steps.action import ActionStep, ApproveActionsStep
from idp.container import Container
from idp.domain.identity import Role
from idp.domain.routing import PolicySnapshot
from idp.infrastructure.db.models import ActionRun, OutboxEvent
from idp.providers.actions.mock_erp import MockERPActionProvider
from idp.providers.actions.webhook import WebhookActionProvider, WebhookSender
from tests.fixtures import files
from tests.integration.conftest import (
    USER_PASSWORD,
    TenantFixture,
    add_user,
    login,
    upload,
)

ALLOW_MOCKS = StaticPolicyResolver(PolicySnapshot(allow_mock_providers=True))


class Hook:
    """A receiving endpoint behind MockTransport; scripted status codes."""

    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status = self.statuses.pop(0) if self.statuses else 200
        return httpx.Response(status, json={"id": "ERP-77"})

    def sender(self) -> WebhookSender:
        return WebhookSender(
            allowed_hosts=frozenset({"hooks.example.test"}),
            client=httpx.AsyncClient(transport=httpx.MockTransport(self)),
        )


def _steps(providers: dict, policy=ALLOW_MOCKS) -> dict:  # type: ignore[no-untyped-def]
    return {
        "approve_actions": ApproveActionsStep(providers=providers, policy=policy),
        "action": ActionStep(providers=providers, policy=policy),
    }


async def _configure(
    client: httpx.AsyncClient, headers: dict[str, str], *, kind: str, requires_approval: bool = True
) -> None:
    config = (
        {"url": "https://hooks.example.test/erp", "secret_env": "IDP_SECRET_HOOK"}
        if kind == "webhook"
        else {}
    )
    created = await client.post(
        "/api/v1/connections",
        headers=headers,
        json={"key": "erp", "name": "ERP", "kind": kind, "config": config},
    )
    assert created.status_code == 201, created.text
    response = await client.post(
        "/api/v1/document-types/from-template",
        headers=headers,
        json={"template_key": "invoice", "publish": False},
    )
    type_id = response.json()["id"]
    draft = (await client.get(f"/api/v1/document-types/{type_id}", headers=headers)).json()
    version = draft["versions"][0]["version"]
    definition = (
        await client.get(f"/api/v1/document-types/{type_id}/schemas/{version}", headers=headers)
    ).json()["definition"]
    definition["actions"] = [
        {
            "name": "post_invoice",
            "connection": "erp",
            "requires_approval": requires_approval,
            "payload": {
                "invoice_number": "field:invoice_number",
                "total": "field:total",
                "lines": "table:lines[]",
                "document": "document:id",
            },
        }
    ]
    saved = await client.put(
        f"/api/v1/document-types/{type_id}/draft", headers=headers, json={"definition": definition}
    )
    assert saved.status_code == 200, saved.text
    assert (
        await client.post(f"/api/v1/document-types/{type_id}/publish", headers=headers)
    ).status_code == 200


@pytest.fixture
async def owner(client: httpx.AsyncClient, acme: TenantFixture) -> dict[str, str]:
    return await login(client, acme.owner_email, acme.owner_password)


async def _doc(client: httpx.AsyncClient, headers: dict[str, str], doc_id: str) -> dict:
    return (await client.get(f"/api/v1/documents/{doc_id}", headers=headers)).json()


async def test_actions_wait_for_approval_then_execute_once(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    acme: TenantFixture,
    container: Container,
    make_runner,
) -> None:  # type: ignore[no-untyped-def]
    await _configure(client, owner, kind="mock_erp")
    steps = _steps({"mock_erp": MockERPActionProvider()})
    body = (await upload(client, owner, files.clean_invoice_pdf())).json()
    job_id, doc_id = uuid.UUID(body["job_id"]), body["document"]["id"]
    assert await make_runner(steps).run(job_id) is RunOutcome.WAITING_FOR_REVIEW
    assert (await _doc(client, owner, doc_id))["status"] == "READY_FOR_ACTION"
    [run] = (await client.get(f"/api/v1/documents/{doc_id}/actions", headers=owner)).json()
    assert run["status"] == "pending_approval"
    assert run["payload"]["invoice_number"] == "INV-2026-00123"
    assert run["payload"]["total"] == "1249.50"
    assert len(run["payload"]["lines"]) == 3
    assert run["is_mock"] is True

    await add_user(container, acme.owner_email, "reviewer@acme.test", Role.REVIEWER)
    reviewer = await login(client, "reviewer@acme.test", USER_PASSWORD)
    denied = await client.post(
        f"/api/v1/documents/{doc_id}/actions/approve", headers=reviewer, json={}
    )
    assert denied.status_code == 403

    approved = await client.post(
        f"/api/v1/documents/{doc_id}/actions/approve", headers=owner, json={"note": "ok"}
    )
    assert approved.status_code == 200
    assert await make_runner(steps).run(job_id) is RunOutcome.SUCCEEDED
    assert (await _doc(client, owner, doc_id))["status"] == "COMPLETED"
    [run] = (await client.get(f"/api/v1/documents/{doc_id}/actions", headers=owner)).json()
    assert run["status"] == "succeeded"
    assert run["external_reference"].startswith("MOCK-ERP-")
    assert run["decision_note"] == "ok"

    events = [e["event_type"] for e in (await client.get("/api/v1/events", headers=owner)).json()]
    assert {"document.ready_for_action", "action.succeeded", "document.completed"} <= set(events)
    replay = await client.post(
        f"/api/v1/documents/{doc_id}/actions/approve", headers=owner, json={}
    )
    assert replay.status_code == 409


async def test_rejected_actions_complete_without_side_effects(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner
) -> None:  # type: ignore[no-untyped-def]
    await _configure(client, owner, kind="mock_erp")
    steps = _steps({"mock_erp": MockERPActionProvider()})
    body = (await upload(client, owner, files.clean_invoice_pdf())).json()
    job_id, doc_id = uuid.UUID(body["job_id"]), body["document"]["id"]
    await make_runner(steps).run(job_id)
    rejected = await client.post(
        f"/api/v1/documents/{doc_id}/actions/reject",
        headers=owner,
        json={"reason": "duplicate posting"},
    )
    assert rejected.status_code == 200
    assert await make_runner(steps).run(job_id) is RunOutcome.SUCCEEDED
    [run] = (await client.get(f"/api/v1/documents/{doc_id}/actions", headers=owner)).json()
    assert (run["status"], run["external_reference"]) == ("rejected", None)
    assert (await _doc(client, owner, doc_id))["status"] == "COMPLETED"


async def test_mock_actions_are_refused_by_policy(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner
) -> None:  # type: ignore[no-untyped-def]
    await _configure(client, owner, kind="mock_erp", requires_approval=False)
    body = (await upload(client, owner, files.clean_invoice_pdf())).json()
    steps = _steps({"mock_erp": MockERPActionProvider()}, StaticPolicyResolver(PolicySnapshot()))
    assert await make_runner(steps).run(uuid.UUID(body["job_id"])) is RunOutcome.FAILED
    doc = await _doc(client, owner, body["document"]["id"])
    assert doc["status"] == "FAILED"


async def test_webhook_retries_transient_errors_with_the_same_idempotency_key(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("IDP_SECRET_HOOK", "hook-secret")
    await _configure(client, owner, kind="webhook", requires_approval=False)
    hook = Hook(503)
    steps = _steps({"webhook": WebhookActionProvider(hook.sender())})
    body = (await upload(client, owner, files.clean_invoice_pdf())).json()
    job_id = uuid.UUID(body["job_id"])
    assert await make_runner(steps).run(job_id) is RunOutcome.RETRY_SCHEDULED
    async with container.session_factory() as session:
        from idp.infrastructure.db.models import ProcessingJob

        job = await session.get(ProcessingJob, job_id)
        assert job is not None
        job.next_attempt_at = None  # retry now
        await session.commit()
    assert await make_runner(steps).run(job_id) is RunOutcome.SUCCEEDED
    assert len(hook.requests) == 2
    keys = {r.headers["Idempotency-Key"] for r in hook.requests}
    assert len(keys) == 1
    async with container.session_factory() as session:
        [run] = (await session.scalars(select(ActionRun))).all()
    assert (run.status, run.attempts, run.external_reference) == ("succeeded", 2, "ERP-77")

    # A later run of the same job never re-executes a succeeded action.
    assert await make_runner(steps).run(job_id) is RunOutcome.NOT_CLAIMED
    assert len(hook.requests) == 2


async def test_outbox_delivers_subscribed_events_once(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("IDP_SECRET_HOOK", "hook-secret")
    created = await client.post(
        "/api/v1/connections",
        headers=owner,
        json={
            "key": "events",
            "name": "Event sink",
            "kind": "webhook",
            "config": {
                "url": "https://hooks.example.test/events",
                "secret_env": "IDP_SECRET_HOOK",
                "events": ["document.completed"],
            },
        },
    )
    assert created.status_code == 201
    await client.post(
        "/api/v1/document-types/from-template",
        headers=owner,
        json={"template_key": "invoice", "publish": True},
    )
    body = (await upload(client, owner, files.clean_invoice_pdf())).json()
    assert await make_runner().run(uuid.UUID(body["job_id"])) is RunOutcome.SUCCEEDED

    hook = Hook(500)
    relay = OutboxRelay(container.session_factory, hook.sender())
    assert await relay.relay() >= 0
    [failed] = [r for r in hook.requests if r.headers["X-IDP-Event"] == "document.completed"]
    async with container.session_factory() as session:
        pending = (
            await session.scalars(
                select(OutboxEvent).where(OutboxEvent.event_type == "document.completed")
            )
        ).one()
        assert pending.published_at is None and pending.attempts == 1 and pending.last_error
        pending.next_attempt_at = pending.created_at  # due again
        await session.commit()
    await relay.relay()
    delivered = [r for r in hook.requests if r.headers["X-IDP-Event"] == "document.completed"]
    assert len(delivered) == 2
    data = json.loads(delivered[-1].content)
    assert data["type"] == "document.completed" and data["data"]["status"] == "COMPLETED"
    assert (
        delivered[-1].headers["Idempotency-Key"] == data["id"] == failed.headers["Idempotency-Key"]
    )
    t, v1 = (p.split("=", 1)[1] for p in delivered[-1].headers["X-IDP-Signature"].split(","))
    assert (
        v1
        == hmac.new(
            b"hook-secret", f"{t}.".encode() + delivered[-1].content, hashlib.sha256
        ).hexdigest()
    )
    await relay.relay()
    assert len([r for r in hook.requests if r.headers["X-IDP-Event"] == "document.completed"]) == 2
    events = (await client.get("/api/v1/events", headers=owner)).json()
    completed = next(e for e in events if e["event_type"] == "document.completed")
    assert completed["published_at"] is not None


async def test_api_keys_for_machine_ingestion(
    client: httpx.AsyncClient, owner: dict[str, str], acme: TenantFixture, container: Container
) -> None:
    created = await client.post("/api/v1/api-keys", headers=owner, json={"name": "scanner"})
    assert created.status_code == 201
    token = created.json()["token"]
    assert token.startswith("idp_")
    assert "token" not in (await client.get("/api/v1/api-keys", headers=owner)).json()[0]
    machine = {"Authorization": f"Bearer {token}"}

    uploaded = await upload(client, machine, files.native_pdf())
    assert uploaded.status_code == 201
    assert uploaded.json()["document"]["source"] == "api"
    listed = (await client.get("/api/v1/documents?source=api", headers=machine)).json()
    assert len(listed["items"]) == 1
    assert (await client.get("/api/v1/reviews", headers=machine)).status_code == 403
    assert (
        await client.post("/api/v1/api-keys", headers=machine, json={"name": "x"})
    ).status_code == 403
    bad_scope = await client.post(
        "/api/v1/api-keys", headers=owner, json={"name": "x", "scopes": ["users:write"]}
    )
    assert bad_scope.status_code == 422

    audit = (await client.get("/api/v1/audit-logs?action=document.received", headers=owner)).json()[
        "items"
    ]
    assert audit and audit[0]["actor_type"] == "api_key"

    await add_user(container, acme.owner_email, "viewer@acme.test", Role.VIEWER)
    viewer = await login(client, "viewer@acme.test", USER_PASSWORD)
    assert (
        await client.post("/api/v1/api-keys", headers=viewer, json={"name": "x"})
    ).status_code == 403

    key_id = created.json()["id"]
    assert (await client.delete(f"/api/v1/api-keys/{key_id}", headers=owner)).status_code == 204
    assert (await client.get("/api/v1/documents", headers=machine)).status_code == 401
    forged = {"Authorization": f"Bearer {token[:-2]}xx"}
    assert (await client.get("/api/v1/documents", headers=forged)).status_code == 401
