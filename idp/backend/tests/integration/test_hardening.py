"""Phase 12: malware verdicts at upload, rate limits, metrics, overview, tracing."""

import io
import uuid

import httpx
from fastapi import FastAPI

from idp.application.jobs import RunOutcome
from idp.container import Container
from idp.domain.documents import ScanStatus
from idp.domain.identity import Role
from idp.infrastructure.scanning import ScannerUnavailableError, ScanVerdict
from idp.infrastructure.telemetry import configure_tracing
from tests.conftest import make_settings
from tests.fixtures import files
from tests.integration.conftest import USER_PASSWORD, TenantFixture, add_user, login, upload


class FixedScanner:
    name = "clamav"

    def __init__(self, verdict: ScanVerdict | None) -> None:
        self.verdict = verdict

    async def scan(self, data: io.BytesIO) -> ScanVerdict:
        if self.verdict is None:
            raise ScannerUnavailableError("Malware scanner unavailable")
        return self.verdict


async def test_infected_uploads_are_refused_and_audited(
    client: httpx.AsyncClient, acme: TenantFixture, container: Container
) -> None:
    headers = await login(client, acme.owner_email, acme.owner_password)
    container.scanner = FixedScanner(
        ScanVerdict(ScanStatus.INFECTED, "clamav", "Eicar-Test-Signature")
    )
    refused = await upload(client, headers, files.native_pdf())
    assert refused.status_code == 422
    audit = (
        await client.get("/api/v1/audit-logs?action=document.malware_rejected", headers=headers)
    ).json()
    assert audit["items"][0]["after"]["signature"] == "Eicar-Test-Signature"
    assert (await client.get("/api/v1/documents", headers=headers)).json()["items"] == []

    container.scanner = FixedScanner(None)
    unavailable = await upload(client, headers, files.native_pdf())
    assert unavailable.status_code == 503

    container.scanner = FixedScanner(ScanVerdict(ScanStatus.CLEAN, "clamav"))
    accepted = await upload(client, headers, files.native_pdf())
    assert accepted.status_code == 201
    detail = (
        await client.get(f"/api/v1/documents/{accepted.json()['document']['id']}", headers=headers)
    ).json()
    assert detail["scan_status"] == "clean"


async def test_per_principal_rate_limit(
    client: httpx.AsyncClient, acme: TenantFixture, container: Container
) -> None:
    headers = await login(client, acme.owner_email, acme.owner_password)
    container.api_limiter._limit = 3
    statuses = [
        (await client.get("/api/v1/documents", headers=headers)).status_code for _ in range(5)
    ]
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3] == 429
    limited = await client.get("/api/v1/documents", headers=headers)
    assert int(limited.headers["Retry-After"]) >= 1


async def test_api_key_uploads_cannot_bypass_the_scanner(
    client: httpx.AsyncClient, acme: TenantFixture, container: Container
) -> None:
    """Machine ingestion (API key) goes through the same scan as interactive uploads."""
    headers = await login(client, acme.owner_email, acme.owner_password)
    token = (await client.post("/api/v1/api-keys", headers=headers, json={"name": "mfp"})).json()[
        "token"
    ]
    machine = {"Authorization": f"Bearer {token}"}
    container.scanner = FixedScanner(ScanVerdict(ScanStatus.INFECTED, "clamav", "Eicar-Signature"))
    assert (await upload(client, machine, files.eicar_pdf())).status_code == 422
    container.scanner = FixedScanner(None)
    assert (await upload(client, machine, files.native_pdf())).status_code == 503
    assert (await client.get("/api/v1/documents", headers=headers)).json()["items"] == []


def test_documents_enter_only_through_the_scanning_ingestion_service() -> None:
    """Structural guard: one ingestion path, and it scans before anything is stored."""
    import ast
    import inspect
    from pathlib import Path

    from idp.application import ingestion

    src = Path(ingestion.__file__).resolve().parents[1]
    constructors = [
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if "IngestionService(" in p.read_text() and p.name != "ingestion.py"
    ]
    assert constructors == ["api/routes/documents.py"]
    tree = ast.parse(inspect.getsource(ingestion.IngestionService.ingest).strip())
    lines = {
        n.func.attr: n.lineno
        for n in sorted(ast.walk(tree), key=lambda n: getattr(n, "lineno", 0), reverse=True)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }  # first occurrence of each call wins
    assert lines["scan"] < lines["put"]  # scanned before stored


async def test_upload_rate_limit_is_enforced_per_principal(
    client: httpx.AsyncClient, acme: TenantFixture, container: Container
) -> None:
    """F1: UPLOAD_RATE_LIMIT_PER_MINUTE bounds uploads; other endpoints are unaffected."""
    headers = await login(client, acme.owner_email, acme.owner_password)
    container.upload_limiter._limit = 2
    statuses = [(await upload(client, headers, files.native_pdf(n))).status_code for n in (1, 2, 3)]
    assert statuses == [201, 201, 429]
    limited = await upload(client, headers, files.native_pdf(4))
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    assert limited.json()["error_category"] == "RATE_LIMITED"
    assert (await client.get("/api/v1/documents", headers=headers)).status_code == 200
    # The budget is per principal: another user of the same tenant is not limited.
    await add_user(container, acme.owner_email, "second@acme.test", Role.OPERATOR)
    other = await login(client, "second@acme.test", USER_PASSWORD)
    assert (await upload(client, other, files.native_pdf(5))).status_code == 201


async def test_metrics_and_overview(
    client: httpx.AsyncClient, acme: TenantFixture, make_runner
) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    body = (await upload(client, headers, files.native_pdf())).json()
    assert await make_runner().run(uuid.UUID(body["job_id"])) in {
        RunOutcome.SUCCEEDED,
        RunOutcome.WAITING_FOR_REVIEW,
    }
    await client.get(f"/api/v1/documents/{body['document']['id']}", headers=headers)
    metrics = (await client.get("/metrics")).text
    assert (
        'idp_http_requests_total{method="GET",route="/documents/{document_id}",status="2xx"}'
        in metrics
    )
    assert body["document"]["id"] not in metrics  # ids never become labels

    overview = (await client.get("/api/v1/system/overview", headers=headers)).json()
    assert sum(overview["documents"].values()) == 1
    assert overview["window_days"] == 30
    assert overview["llm"] == {"calls": 0, "cost": 0.0, "tokens": 0}


async def test_metrics_token_and_tracing_off_by_default(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from idp.main import create_app

    settings = make_settings(tmp_path, metrics_token="scrape-secret-123")
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/metrics")).status_code == 401
        ok = await c.get("/metrics", headers={"Authorization": "Bearer scrape-secret-123"})
        assert ok.status_code == 200
    assert configure_tracing(FastAPI(), settings) is False
