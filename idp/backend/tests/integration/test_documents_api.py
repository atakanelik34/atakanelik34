import io
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from idp.container import Container
from idp.domain.identity import Role
from idp.infrastructure.db.models import AuditLog, Document, ProcessingJob
from tests.conftest import make_settings
from tests.fixtures import files
from tests.integration.conftest import (
    USER_PASSWORD,
    RecordingQueue,
    TenantFixture,
    add_user,
    bootstrap_tenant,
    login,
    upload,
)


@pytest.fixture
async def owner(client: httpx.AsyncClient, acme: TenantFixture) -> dict[str, str]:
    return await login(client, acme.owner_email, acme.owner_password)


async def test_upload_stores_queues_and_audits(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    queue: RecordingQueue,
) -> None:
    data = files.native_pdf(2)
    response = await upload(client, owner, data, filename="../../etc/Invoice 7.pdf")
    assert response.status_code == 201, response.text
    body = response.json()
    doc = body["document"]
    assert doc["status"] == "QUEUED"
    assert doc["original_filename"] == "Invoice 7.pdf"
    assert doc["detected_mime_type"] == "application/pdf"
    assert doc["size_bytes"] == len(data)

    # Dispatched exactly once, after commit, with the initial token.
    assert queue.messages == [(uuid.UUID(body["job_id"]), 0, 0)]

    async with container.session_factory() as session:
        document = await session.get(Document, uuid.UUID(doc["id"]))
        assert document is not None
        assert document.scan_status == "not_scanned"
        assert "Invoice" not in document.storage_key
        assert document.storage_key.startswith(f"tenants/{document.tenant_id}/documents/")
        sink = io.BytesIO()
        await container.storage.download(document.storage_key, sink)
        assert sink.getvalue() == data
        job = await session.get(ProcessingJob, uuid.UUID(body["job_id"]))
        assert job is not None
        assert (job.status.value, job.trigger, job.attempts) == ("QUEUED", "upload", 0)
        actions = (
            await session.scalars(select(AuditLog.action).where(AuditLog.entity_id == doc["id"]))
        ).all()
    assert "document.received" in actions
    assert "document.status_changed" in actions


async def test_duplicate_upload_is_rejected_with_existing_id(
    client: httpx.AsyncClient, owner: dict[str, str], queue: RecordingQueue
) -> None:
    data = files.native_pdf()
    first = await upload(client, owner, data)
    second = await upload(client, owner, data, filename="renamed.pdf")
    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "duplicate_document"
    assert body["details"]["existing_document_id"] == first.json()["document"]["id"]
    assert len(queue.messages) == 1


async def test_losing_the_duplicate_upload_race_answers_409(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    queue: RecordingQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F12: the pre-check misses (both uploads passed it before either committed), so the
    second one hits the unique constraint; it must answer 409, not 500."""
    from idp.application.ingestion import IngestionService

    data = files.native_pdf()
    first = await upload(client, owner, data)
    original = IngestionService._find_duplicate
    calls: list[int] = []

    async def race(self, *args):  # type: ignore[no-untyped-def]
        calls.append(1)
        return None if len(calls) == 1 else await original(self, *args)

    monkeypatch.setattr(IngestionService, "_find_duplicate", race)
    second = await upload(client, owner, data)
    assert len(calls) == 2  # pre-check missed, then the unique-constraint path ran
    assert second.status_code == 409
    assert second.json()["details"]["existing_document_id"] == first.json()["document"]["id"]
    assert len(queue.messages) == 1


@pytest.mark.parametrize(
    ("data", "filename", "content_type", "status", "code"),
    [
        (b"just some text", "notes.pdf", "application/pdf", 415, "unsupported_media_type"),
        (
            b"PK\x03\x04 office zip",
            "x.docx",
            "application/octet-stream",
            415,
            "unsupported_media_type",
        ),
        (b"", "empty.pdf", "application/pdf", 422, "document_error"),
    ],
)
async def test_rejected_files_are_classified_and_not_stored(  # noqa: PLR0917
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    data: bytes,
    filename: str,
    content_type: str,
    status: int,
    code: str,
) -> None:
    response = await upload(client, owner, data, filename, content_type)
    assert response.status_code == status
    assert response.json()["code"] == code
    async with container.session_factory() as session:
        assert (await session.scalars(select(Document))).all() == []


async def test_declared_type_is_advisory(client: httpx.AsyncClient, owner: dict[str, str]) -> None:
    response = await upload(client, owner, files.png(), "scan.pdf", "application/pdf")
    assert response.status_code == 201
    assert response.json()["document"]["detected_mime_type"] == "image/png"


async def test_missing_file_field(client: httpx.AsyncClient, owner: dict[str, str]) -> None:
    response = await client.post(
        "/api/v1/documents", headers=owner, files={"other": ("a.pdf", b"x", "application/pdf")}
    )
    assert response.status_code == 422


async def test_size_limits(
    app: FastAPI, client: httpx.AsyncClient, owner: dict[str, str], tmp_path: Path
) -> None:
    app.state.container.settings = make_settings(tmp_path, max_upload_bytes=2048)
    # Small enough to pass the header pre-check, too big for the limit.
    medium = await upload(client, owner, files.native_pdf() + b"0" * 4096)
    assert medium.status_code == 413
    # Rejected from Content-Length before the body is parsed.
    large = await upload(client, owner, b"%PDF-1.7" + b"0" * 200_000)
    assert large.status_code == 413
    assert large.json()["error_category"] == "VALIDATION_ERROR"


async def test_rbac_viewer_reads_but_cannot_write(
    client: httpx.AsyncClient, container: Container, acme: TenantFixture, owner: dict[str, str]
) -> None:
    await upload(client, owner, files.native_pdf())
    await add_user(container, acme.owner_email, "viewer@acme.test", Role.VIEWER)
    viewer = await login(client, "viewer@acme.test", USER_PASSWORD)
    assert (await upload(client, viewer, files.scanned_pdf())).status_code == 403
    listed = await client.get("/api/v1/documents", headers=viewer)
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1


async def test_tenant_isolation(
    client: httpx.AsyncClient, container: Container, owner: dict[str, str]
) -> None:
    doc_id = (await upload(client, owner, files.native_pdf())).json()["document"]["id"]
    globex = await bootstrap_tenant(container, "globex")
    other = await login(client, globex.owner_email, globex.owner_password)

    for path in ("", "/timeline", "/download"):
        assert (
            await client.get(f"/api/v1/documents/{doc_id}{path}", headers=other)
        ).status_code == 404
    assert (
        await client.post(f"/api/v1/documents/{doc_id}/process", headers=other)
    ).status_code == 404
    assert (await client.delete(f"/api/v1/documents/{doc_id}", headers=other)).status_code == 404
    assert (await client.get("/api/v1/documents", headers=other)).json()["items"] == []
    # The same bytes are not a duplicate in another tenant.
    assert (await upload(client, other, files.native_pdf())).status_code == 201


async def test_list_pagination_and_filter(client: httpx.AsyncClient, owner: dict[str, str]) -> None:
    for n in range(3):
        assert (await upload(client, owner, files.native_pdf(n + 1))).status_code == 201
    first = (await client.get("/api/v1/documents?limit=2", headers=owner)).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    second = (
        await client.get(f"/api/v1/documents?limit=2&cursor={first['next_cursor']}", headers=owner)
    ).json()
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
    ids = [d["id"] for d in first["items"] + second["items"]]
    assert len(set(ids)) == 3

    queued = (await client.get("/api/v1/documents?status=QUEUED", headers=owner)).json()
    assert len(queued["items"]) == 3
    done = (await client.get("/api/v1/documents?status=COMPLETED", headers=owner)).json()
    assert done["items"] == []
    bad = await client.get("/api/v1/documents?cursor=garbage", headers=owner)
    assert bad.status_code == 422


async def test_signed_download_is_audited(
    client: httpx.AsyncClient, owner: dict[str, str], container: Container
) -> None:
    data = files.png()
    doc_id = (await upload(client, owner, data, "scan.png", "image/png")).json()["document"]["id"]
    link = (await client.get(f"/api/v1/documents/{doc_id}/download", headers=owner)).json()
    url = urlparse(link["url"])
    fetched = await client.get(f"{url.path}?{url.query}")
    assert fetched.status_code == 200
    assert fetched.content == data
    async with container.session_factory() as session:
        actions = (
            await session.scalars(select(AuditLog.action).where(AuditLog.entity_id == doc_id))
        ).all()
    assert "document.downloaded" in actions


async def test_reprocess_and_delete_respect_active_jobs(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    doc_id = (await upload(client, owner, files.native_pdf())).json()["document"]["id"]
    busy = await client.post(f"/api/v1/documents/{doc_id}/process", headers=owner)
    assert busy.status_code == 409
    assert (await client.delete(f"/api/v1/documents/{doc_id}", headers=owner)).status_code == 409
