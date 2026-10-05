"""Validation -> human review -> resume/reject/send back, end to end."""

import uuid

import httpx
import pytest
from sqlalchemy import select

from idp.application.jobs import RunOutcome
from idp.container import Container
from idp.domain.identity import Role
from idp.infrastructure.db.models import ProcessingJob, ReviewAction
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
    headers = await login(client, acme.owner_email, acme.owner_password)
    response = await client.post(
        "/api/v1/document-types/from-template",
        headers=headers,
        json={"template_key": "invoice", "publish": True},
    )
    assert response.status_code == 201
    return headers


async def _run(client, headers, data, make_runner) -> tuple[str, uuid.UUID, RunOutcome]:  # type: ignore[no-untyped-def]
    body = (await upload(client, headers, data)).json()
    job_id = uuid.UUID(body["job_id"])
    return body["document"]["id"], job_id, await make_runner().run(job_id)


async def _doc(client: httpx.AsyncClient, headers: dict[str, str], doc_id: str) -> dict:
    return (await client.get(f"/api/v1/documents/{doc_id}", headers=headers)).json()


async def _task_for(client: httpx.AsyncClient, headers: dict[str, str], doc_id: str) -> dict:
    tasks = (await client.get("/api/v1/reviews", headers=headers)).json()
    task = next(t for t in tasks if t["document_id"] == doc_id)
    return (await client.get(f"/api/v1/reviews/{task['id']}", headers=headers)).json()


async def test_clean_invoice_needs_no_human(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner
) -> None:  # type: ignore[no-untyped-def]
    doc_id, _, outcome = await _run(client, owner, files.clean_invoice_pdf(), make_runner)
    assert outcome is RunOutcome.SUCCEEDED
    assert (await _doc(client, owner, doc_id))["status"] == "COMPLETED"
    result = (await client.get(f"/api/v1/documents/{doc_id}/extraction", headers=owner)).json()
    outcomes = {v["rule"]: v["outcome"] for v in result["parts"][0]["validation"]}
    assert outcomes["rule:0:sum"] == "PASS"
    assert outcomes["field:iban:iban:0"] == "PASS"
    assert "REQUIRES_HUMAN" not in outcomes.values()
    assert (await client.get("/api/v1/reviews", headers=owner)).json() == []


async def test_low_confidence_goes_to_review_then_resumes_after_correction(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner,  # type: ignore[no-untyped-def]
    queue: RecordingQueue,
) -> None:
    doc_id, job_id, outcome = await _run(client, owner, files.invoice_pdf(), make_runner)
    assert outcome is RunOutcome.WAITING_FOR_REVIEW
    assert (await _doc(client, owner, doc_id))["status"] == "WAITING_FOR_HUMAN"
    async with container.session_factory() as session:
        job = await session.get(ProcessingJob, job_id)
        assert job is not None
        assert job.status.value == "WAITING_FOR_REVIEW"
    # Waiting for a human is not a failure, and the sweeper leaves it alone.
    assert (
        await client.post(f"/api/v1/documents/{doc_id}/process", headers=owner)
    ).status_code == 409

    detail = await _task_for(client, owner, doc_id)
    task_id = detail["task"]["id"]
    assert {r["rule_id"] for r in detail["task"]["reasons"]} == {"field:vendor_name:confidence"}
    vendor = detail["parts"][0]["fields"]["vendor_name"]
    assert vendor["below_threshold"] is True

    assert (await client.post(f"/api/v1/reviews/{task_id}/claim", headers=owner)).status_code == 204
    bad = await client.post(
        f"/api/v1/reviews/{task_id}/fields/{detail['parts'][0]['fields']['due_date']['id']}",
        headers=owner,
        json={"action": "edit", "value": "not a date"},
    )
    assert bad.status_code == 422
    fixed = await client.post(
        f"/api/v1/reviews/{task_id}/fields/{vendor['id']}",
        headers=owner,
        json={
            "action": "edit",
            "value": "ACME Industrial Supplies GmbH",
            "reason": "confirmed from letterhead",
        },
    )
    assert fixed.status_code == 204
    after = (await client.get(f"/api/v1/reviews/{task_id}", headers=owner)).json()
    field = after["parts"][0]["fields"]["vendor_name"]
    assert (field["status"], field["confidence"]) == ("corrected", 1.0)
    assert not [
        v for v in after["parts"][0]["validation"] if v["outcome"] in ("FAIL", "REQUIRES_HUMAN")
    ]

    queue.messages.clear()
    assert (
        await client.post(f"/api/v1/reviews/{task_id}/approve", headers=owner, json={"note": "ok"})
    ).status_code == 204
    assert queue.messages == [(job_id, 1, 0)]
    assert await make_runner().run(job_id) is RunOutcome.SUCCEEDED
    assert (await _doc(client, owner, doc_id))["status"] == "COMPLETED"

    timeline = (await client.get(f"/api/v1/documents/{doc_id}/timeline", headers=owner)).json()
    review_step = [s for s in timeline["jobs"][0]["steps"] if s["step_key"] == "review"]
    assert [s["status"] for s in review_step] == ["SUCCEEDED"]  # not re-run after approval
    moves = [c["to_status"] for c in timeline["status_changes"]]
    assert moves[-3:] == ["WAITING_FOR_HUMAN", "PROCESSING", "COMPLETED"]

    async with container.session_factory() as session:
        actions = (
            await session.scalars(select(ReviewAction.action).order_by(ReviewAction.created_at))
        ).all()
        correction = await session.scalar(select(ReviewAction).where(ReviewAction.action == "edit"))
    assert actions == ["claim", "edit", "approve"]
    assert correction is not None
    assert (
        correction.original_value == "ACME Industrial Supplies GmbH"
    )  # heuristic value was right, but unsure
    assert correction.reason == "confirmed from letterhead"


async def test_inconsistent_totals_can_be_rejected(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner
) -> None:  # type: ignore[no-untyped-def]
    doc_id, _, outcome = await _run(
        client, owner, files.clean_invoice_pdf(total="1,300.00 EUR"), make_runner
    )
    assert outcome is RunOutcome.WAITING_FOR_REVIEW
    detail = await _task_for(client, owner, doc_id)
    assert [r["rule_id"] for r in detail["task"]["reasons"]] == ["rule:0:sum"]
    task_id = detail["task"]["id"]
    short = await client.post(
        f"/api/v1/reviews/{task_id}/reject", headers=owner, json={"reason": "x"}
    )
    assert short.status_code == 422
    rejected = await client.post(
        f"/api/v1/reviews/{task_id}/reject", headers=owner, json={"reason": "duplicate invoice"}
    )
    assert rejected.status_code == 204
    assert (await _doc(client, owner, doc_id))["status"] == "REJECTED"
    again = await client.post(f"/api/v1/reviews/{task_id}/approve", headers=owner, json={})
    assert again.status_code == 409


async def test_send_back_starts_a_new_run(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner
) -> None:  # type: ignore[no-untyped-def]
    doc_id, job_id, _ = await _run(client, owner, files.invoice_pdf(), make_runner)
    task_id = (await _task_for(client, owner, doc_id))["task"]["id"]
    response = await client.post(
        f"/api/v1/reviews/{task_id}/send-back", headers=owner, json={"reason": "wrong template"}
    )
    assert response.status_code == 202
    new_job = uuid.UUID(response.json()["job_id"])
    assert new_job != job_id
    assert (await _doc(client, owner, doc_id))["status"] == "QUEUED"
    timeline = (await client.get(f"/api/v1/documents/{doc_id}/timeline", headers=owner)).json()
    assert [j["status"] for j in timeline["jobs"]] == ["QUEUED", "CANCELLED"]
    assert timeline["jobs"][0]["trigger"] == "review_send_back"


async def test_line_item_rows_can_be_added_and_removed(
    client: httpx.AsyncClient, owner: dict[str, str], make_runner
) -> None:  # type: ignore[no-untyped-def]
    doc_id, _, _ = await _run(client, owner, files.invoice_pdf(), make_runner)
    detail = await _task_for(client, owner, doc_id)
    task_id, part = detail["task"]["id"], detail["parts"][0]
    added = await client.post(
        f"/api/v1/reviews/{task_id}/rows",
        headers=owner,
        json={
            "part_id": part["part_id"],
            "array_path": "lines[]",
            "cells": {"description": "Freight", "total": "10,00"},
        },
    )
    assert added.status_code == 201
    row_id = added.json()["row_id"]
    rows = (await client.get(f"/api/v1/reviews/{task_id}", headers=owner)).json()["parts"][0][
        "tables"
    ]["lines[]"]
    new = next(r for r in rows if r["row_id"] == row_id)
    assert new["cells"]["total"]["value"] == "10.00"
    # Adding a line breaks "lines add up to subtotal" -> warning, visible immediately.
    validation = (await client.get(f"/api/v1/reviews/{task_id}", headers=owner)).json()["parts"][0][
        "validation"
    ]
    assert next(v for v in validation if v["rule_type"] == "line_items_sum")["outcome"] == "WARNING"

    deleted = await client.delete(
        f"/api/v1/reviews/{task_id}/rows/{row_id}?part_id={part['part_id']}", headers=owner
    )
    assert deleted.status_code == 204
    validation = (await client.get(f"/api/v1/reviews/{task_id}", headers=owner)).json()["parts"][0][
        "validation"
    ]
    assert next(v for v in validation if v["rule_type"] == "line_items_sum")["outcome"] == "PASS"


async def test_review_permissions_and_isolation(
    client: httpx.AsyncClient,
    container: Container,
    acme: TenantFixture,
    owner: dict[str, str],
    make_runner,  # type: ignore[no-untyped-def]
) -> None:
    doc_id, _, _ = await _run(client, owner, files.invoice_pdf(), make_runner)
    task_id = (await _task_for(client, owner, doc_id))["task"]["id"]
    await add_user(container, acme.owner_email, "viewer@acme.test", Role.VIEWER)
    await add_user(container, acme.owner_email, "rev1@acme.test", Role.REVIEWER)
    await add_user(container, acme.owner_email, "rev2@acme.test", Role.REVIEWER)
    viewer = await login(client, "viewer@acme.test", USER_PASSWORD)
    rev1 = await login(client, "rev1@acme.test", USER_PASSWORD)
    rev2 = await login(client, "rev2@acme.test", USER_PASSWORD)

    assert (await client.get(f"/api/v1/reviews/{task_id}", headers=viewer)).status_code == 200
    assert (
        await client.post(f"/api/v1/reviews/{task_id}/claim", headers=viewer)
    ).status_code == 403
    assert (await client.post(f"/api/v1/reviews/{task_id}/claim", headers=rev1)).status_code == 204
    taken = await client.post(f"/api/v1/reviews/{task_id}/approve", headers=rev2, json={})
    assert taken.status_code == 409
    assert "another reviewer" in taken.json()["detail"]

    globex = await bootstrap_tenant(container, "globex")
    other = await login(client, globex.owner_email, globex.owner_password)
    assert (await client.get(f"/api/v1/reviews/{task_id}", headers=other)).status_code == 404
    assert (await client.get("/api/v1/reviews", headers=other)).json() == []


async def test_audit_log_api(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    acme: TenantFixture,
    make_runner,
) -> None:  # type: ignore[no-untyped-def]
    doc_id, _, _ = await _run(client, owner, files.invoice_pdf(), make_runner)
    task_id = (await _task_for(client, owner, doc_id))["task"]["id"]
    await client.post(f"/api/v1/reviews/{task_id}/claim", headers=owner)
    page = (await client.get("/api/v1/audit-logs?action=review.", headers=owner)).json()
    assert [i["action"] for i in page["items"]] == ["review.claimed", "review.requested"]
    assert page["items"][0]["actor_email"] == acme.owner_email
    by_doc = (
        await client.get(f"/api/v1/audit-logs?entity_id={doc_id}&limit=2", headers=owner)
    ).json()
    assert len(by_doc["items"]) == 2
    assert by_doc["next_cursor"]
    rest = (
        await client.get(
            f"/api/v1/audit-logs?entity_id={doc_id}&cursor={by_doc['next_cursor']}", headers=owner
        )
    ).json()
    assert {i["id"] for i in rest["items"]}.isdisjoint({i["id"] for i in by_doc["items"]})

    await add_user(container, acme.owner_email, "rev@acme.test", Role.REVIEWER)
    reviewer = await login(client, "rev@acme.test", USER_PASSWORD)
    assert (await client.get("/api/v1/audit-logs", headers=reviewer)).status_code == 403


class EchoOCR:
    """Perfect OCR: returns the clean invoice's own text lines at confidence 1.0, so every
    field and rule would pass - only the fact that the page was scanned remains."""

    name, version, is_mock, locality = "echo-ocr", "1", True, "local"

    def __init__(self, lines) -> None:  # type: ignore[no-untyped-def]
        self.lines = lines

    async def recognize(self, image, *, page_number: int):  # type: ignore[no-untyped-def]
        from dataclasses import replace

        from idp.domain.geometry import Line
        from idp.providers.ocr.base import OCRPage

        lines = tuple(
            Line(id=line.id, words=tuple(replace(w, confidence=1.0) for w in line.words))
            for line in self.lines
        )
        return OCRPage(lines=lines, mean_confidence=1.0)


async def _native_lines(digitizer, tmp_path):  # type: ignore[no-untyped-def]
    from idp.domain.documents import PDF

    source = tmp_path / "clean.pdf"
    source.write_bytes(files.clean_invoice_pdf())
    result = await digitizer.digitize(source, PDF, tmp_path)
    return [line for block in result.pages[0].layout.blocks for line in block.lines]


async def test_scanned_documents_always_need_a_human(
    *,
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    digitizer,
    make_runner,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    """P0-2: a scanned page is reviewed even when OCR and every rule would let it through."""
    from idp.application.steps.digitize import DigitizeStep
    from idp.providers.digitization.local import HybridDigitizer

    echo = HybridDigitizer(
        ocr=EchoOCR(await _native_lines(digitizer, tmp_path)),  # type: ignore[arg-type]
        workers=1,
        timeout_seconds=60,
        max_pages=50,
        memory_limit_mb=2048,
        render_dpi=72,
        ocr_dpi=150,
        min_native_quality=0.5,
    )
    try:
        steps = {"digitize": DigitizeStep(storage=container.storage, digitizer=echo, tmp_dir=None)}
        body = (await upload(client, owner, files.scanned_pdf(1))).json()
        job_id, doc_id = uuid.UUID(body["job_id"]), body["document"]["id"]
        assert await make_runner(steps).run(job_id) is RunOutcome.WAITING_FOR_REVIEW
    finally:
        echo.close()
    result = (await client.get(f"/api/v1/documents/{doc_id}/extraction", headers=owner)).json()
    part = result["parts"][0]
    assert part["classification"]["document_type"] == "invoice"
    assert part["fields"]["total"]["value"] == "1249.50"  # OCR text was perfect...
    blocking = {v["rule"] for v in part["validation"] if v["outcome"] in ("REQUIRES_HUMAN", "FAIL")}
    assert blocking == {"part:ocr"}  # ...and the scan alone requires a person
    task = (await client.get("/api/v1/reviews", headers=owner)).json()[0]
    await client.post(f"/api/v1/reviews/{task['id']}/claim", headers=owner)
    approved = await client.post(
        f"/api/v1/reviews/{task['id']}/approve", headers=owner, json={"note": "checked scan"}
    )
    assert approved.status_code == 204
    assert await make_runner().run(job_id) is RunOutcome.SUCCEEDED
    async with container.session_factory() as session:
        approval = (
            await session.scalars(select(ReviewAction).where(ReviewAction.action == "approve"))
        ).one()
    assert approval.corrected_value == {"overridden_rules": ["part:ocr"]}
