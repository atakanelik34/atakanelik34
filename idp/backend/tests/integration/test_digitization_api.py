import uuid
from urllib.parse import urlparse

import httpx
import pytest

from idp.application.jobs import RunOutcome
from tests.fixtures import files
from tests.integration.conftest import TenantFixture, login, upload


@pytest.fixture
async def owner(client: httpx.AsyncClient, acme: TenantFixture) -> dict[str, str]:
    return await login(client, acme.owner_email, acme.owner_password)


async def test_digitized_pages_expose_geometry_and_images(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    make_runner,  # type: ignore[no-untyped-def]
) -> None:
    data = files.make_pdf(["ACME Ltd. Invoice INV-2026-00123 total due 1,250.00 EUR", None])
    body = (await upload(client, owner, data)).json()
    doc_id = body["document"]["id"]
    assert await make_runner().run(uuid.UUID(body["job_id"])) is RunOutcome.SUCCEEDED

    detail = (await client.get(f"/api/v1/documents/{doc_id}", headers=owner)).json()
    page1, page2 = detail["pages"]
    assert (page1["text_source"], page1["ocr_status"]) == ("native", "not_needed")
    assert page1["text_quality"] > 0.8 and page1["word_count"] >= 8
    # No OCR engine configured in tests: reported honestly, nothing invented.
    assert (page2["text_source"], page2["ocr_status"]) == ("none", "not_configured")

    layout = (await client.get(f"/api/v1/documents/{doc_id}/pages/1/layout", headers=owner)).json()
    words = [w for line in layout["lines"] for w in line["words"]]
    target = next(w for w in words if w["text"] == "INV-2026-00123")
    assert all(0 <= v <= 1 for v in target["bbox"])
    assert layout["lines"][0]["id"] == "p1-l0"

    image = (await client.get(f"/api/v1/documents/{doc_id}/pages/1/image", headers=owner)).json()
    url = urlparse(image["url"])
    fetched = await client.get(f"{url.path}?{url.query}")
    assert fetched.status_code == 200
    assert fetched.content[:4] == b"RIFF"  # WEBP
    assert image["width"] > 0

    timeline = (await client.get(f"/api/v1/documents/{doc_id}/timeline", headers=owner)).json()
    digitize = next(s for s in timeline["jobs"][0]["steps"] if s["step_key"] == "digitize")
    assert digitize["metrics"]["pages_native"] == 1
    assert digitize["metrics"]["ocr_not_configured_pages"] == 1
    assert digitize["metrics"]["ocr_engine"] == "not_configured"

    missing = await client.get(f"/api/v1/documents/{doc_id}/pages/9/layout", headers=owner)
    assert missing.status_code == 404
