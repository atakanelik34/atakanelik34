import hashlib
import hmac
import json

import httpx
import pytest

from idp.domain.actions import ActionContext, build_payload
from idp.domain.identity import Permission, Principal, Role
from idp.domain.taxonomy import ActionSpec, SchemaDefinition
from idp.infrastructure.db.models import Connection
from idp.providers.actions.base import ActionError, ActionRequest, sign
from idp.providers.actions.email import EmailActionProvider
from idp.providers.actions.mock_erp import MockERPActionProvider
from idp.providers.actions.webhook import WebhookActionProvider, WebhookSender
from tests.conftest import make_settings

CTX = ActionContext(
    document={"id": "d1", "filename": "inv.pdf", "type": "invoice", "pages": [1, 1]},
    fields={"invoice_number": "INV-1", "total": "10.00"},
    tables={"lines[]": [{"description": "Pump", "total": "10.00"}]},
    enrichment={"vendor": {"key": "V-1", "payment_terms": "NET30"}},
)


def test_payload_is_built_only_from_declared_references() -> None:
    spec = ActionSpec(
        name="post",
        connection="erp",
        payload={
            "number": "field:invoice_number",
            "vendor": "enrichment:vendor.key",
            "vendor_all": "enrichment:vendor",
            "lines": "table:lines[]",
            "doc": "document:id",
            "source": "const:idp",
            "missing": "field:due_date",
        },
    )
    assert build_payload(spec, CTX) == {
        "number": "INV-1",
        "vendor": "V-1",
        "vendor_all": {"key": "V-1", "payment_terms": "NET30"},
        "lines": [{"description": "Pump", "total": "10.00"}],
        "doc": "d1",
        "source": "idp",
        "missing": None,
    }
    standard = build_payload(ActionSpec(name="post", connection="erp"), CTX)
    assert set(standard) == {"document", "fields", "tables", "enrichment"}


def test_action_references_are_validated_at_save() -> None:
    with pytest.raises(ValueError, match="invalid payload reference"):
        ActionSpec(name="post", connection="erp", payload={"x": "python:os.system('id')"})
    with pytest.raises(ValueError, match="unknown field"):
        SchemaDefinition.model_validate(
            {
                "fields": [],
                "actions": [{"name": "post", "connection": "erp", "payload": {"x": "field:nope"}}],
            }
        )


def test_api_key_principal_never_exceeds_its_creator() -> None:
    import uuid

    key = Principal(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="e",
        role=Role.REVIEWER,
        api_key_id=uuid.uuid4(),
        scopes=frozenset({Permission.DOCUMENTS_READ, Permission.DOCUMENTS_WRITE}),
    )
    assert key.permissions == frozenset({Permission.DOCUMENTS_READ})  # reviewers cannot upload


def _webhook(handler) -> tuple[WebhookActionProvider, Connection]:  # type: ignore[no-untyped-def]
    sender = WebhookSender(
        allowed_hosts=frozenset({"hooks.example.test"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    connection = Connection(
        key="erp",
        name="ERP",
        kind="webhook",
        config={"url": "https://hooks.example.test/in", "secret_env": "IDP_SECRET_HOOK"},
    )
    return WebhookActionProvider(sender), connection


REQUEST = ActionRequest(
    idempotency_key="j:p:post", action="post", document_id="d1", payload={"a": 1}
)


async def test_webhook_is_signed_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IDP_SECRET_HOOK", "hook-secret")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": "ERP-42"})

    provider, connection = _webhook(handler)
    outcome = await provider.execute(connection, REQUEST)
    assert outcome.external_reference == "ERP-42"
    sent = seen[0]
    assert sent.headers["Idempotency-Key"] == "j:p:post"
    t, v1 = (part.split("=", 1)[1] for part in sent.headers["X-IDP-Signature"].split(","))
    expected = hmac.new(b"hook-secret", f"{t}.".encode() + sent.content, hashlib.sha256).hexdigest()
    assert v1 == expected
    assert json.loads(sent.content)["payload"] == {"a": 1}
    assert sign("s", b"{}", 1).startswith("t=1,v1=")


@pytest.mark.parametrize(("status", "transient"), [(503, True), (429, True), (400, False)])
async def test_webhook_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, transient: bool
) -> None:
    monkeypatch.setenv("IDP_SECRET_HOOK", "x")
    provider, connection = _webhook(lambda r: httpx.Response(status))
    with pytest.raises(ActionError) as caught:
        await provider.execute(connection, REQUEST)
    assert caught.value.transient is transient


async def test_webhook_duplicate_is_success_and_host_and_secret_are_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, connection = _webhook(lambda r: httpx.Response(409))
    with pytest.raises(ActionError, match="secret_not_configured"):
        await provider.execute(connection, REQUEST)
    monkeypatch.setenv("IDP_SECRET_HOOK", "x")
    assert (await provider.execute(connection, REQUEST)).response == {"status": 409}
    connection.config = {"url": "https://evil.example/in", "secret_env": "IDP_SECRET_HOOK"}
    with pytest.raises(ActionError, match="host_not_allowed"):
        await provider.execute(connection, REQUEST)


async def test_email_without_smtp_is_not_configured_and_mock_is_deterministic(tmp_path) -> None:  # type: ignore[no-untyped-def]
    email = EmailActionProvider(make_settings(tmp_path))
    assert email.configured() is False
    mock = MockERPActionProvider()
    first = await mock.execute(Connection(key="m", name="m", kind="mock_erp", config={}), REQUEST)
    second = await mock.execute(Connection(key="m", name="m", kind="mock_erp", config={}), REQUEST)
    assert first.external_reference == second.external_reference
    assert first.external_reference and first.external_reference.startswith("MOCK-ERP-")
