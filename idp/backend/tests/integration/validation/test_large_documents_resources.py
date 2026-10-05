"""Scenarios 3, 4, 12, 13: large PDFs, high page counts, resource exhaustion, CPU/RAM
under concurrent documents. Uses production limits (2,000 pages, 120 s parser
timeout, 150 dpi rendering, 2 GiB parser memory limit)."""

import asyncio
import struct
import uuid
import zlib
from pathlib import Path

import pytest
from sqlalchemy import text

from idp.application.jobs import RunOutcome
from idp.application.steps.digitize import DigitizeStep
from idp.application.steps.probe import ProbeStep
from idp.container import Container
from tests.fixtures import files
from tests.integration.conftest import login, upload
from tests.integration.validation.conftest import (
    PROD_MAX_PAGES,
    FaultyOCR,
    ResourceSampler,
    Timer,
    job_row,
    make_due,
    noise_jpeg_pdf,
)

MAX_UPLOAD = 52_428_800  # MAX_UPLOAD_BYTES default (= nginx client_max_body_size 50m)


async def _step_durations(container: Container, job_id: uuid.UUID) -> dict[str, int]:
    async with container.session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT step_key, max(duration_ms) FROM processing_steps WHERE job_id = :id AND status = 'SUCCEEDED' GROUP BY step_key"
                ),
                {"id": job_id},
            )
        ).all()
    return {row[0]: row[1] for row in rows}


async def _run_to_end(
    container: Container, runner, job_id: uuid.UUID, max_runs: int = 5
) -> list[str]:  # type: ignore[no-untyped-def]
    outcomes = []
    for _ in range(max_runs):
        outcome = await runner.run(job_id)
        outcomes.append(outcome.value)
        if outcome is not RunOutcome.RETRY_SCHEDULED:
            break
        await make_due(container, job_id)
    return outcomes


# 3 --------------------------------------------------------------------------------------
async def test_large_pdf_near_the_upload_limit(
    client, acme, container, ocr_handlers, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    data = noise_jpeg_pdf(pages=7)  # ~46 MB of incompressible scanned pages
    assert len(data) < MAX_UPLOAD
    with Timer() as up:
        response = await upload(client, headers, data)
    assert response.status_code == 201
    job_id = uuid.UUID(response.json()["job_id"])
    async with ResourceSampler() as res:
        outcomes = await _run_to_end(container, runner_factory(ocr_handlers(FaultyOCR())), job_id)
    assert outcomes[-1] in ("succeeded", "waiting_for_review"), outcomes
    record(
        "3 large PDF near limit",
        size_mb=round(len(data) / 1e6, 1),
        upload_s=up.seconds,
        outcomes=outcomes,
        step_ms=await _step_durations(container, job_id),
        **res.summary(),
    )


async def test_pdf_over_the_upload_limit_is_refused_without_storing(
    client, acme, container, record
) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    data = noise_jpeg_pdf(pages=13)
    assert len(data) > MAX_UPLOAD
    response = await upload(client, headers, data)
    async with container.session_factory() as session:
        documents = await session.scalar(text("SELECT count(*) FROM documents"))
    assert response.status_code == 413 and documents == 0
    record("3 over-limit PDF", size_mb=round(len(data) / 1e6, 1), status=response.status_code)


# 4 --------------------------------------------------------------------------------------
# 1,200 pages: ~130 s of rendering, beyond the old single 120 s parser timeout (F15).
@pytest.mark.parametrize("pages", [100, 500, 1000, 1200])
async def test_high_page_count_native_pdf(
    client, acme, container, ocr_handlers, runner_factory, record, pages: int
) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    data = files.make_pdf(
        [f"Statement page {n}\nAccount 4711 balance {n}.00 EUR" for n in range(pages)]
    )
    job_id = uuid.UUID((await upload(client, headers, data)).json()["job_id"])
    async with ResourceSampler() as res:
        outcomes = await _run_to_end(container, runner_factory(ocr_handlers(None)), job_id)
    steps = await _step_durations(container, job_id)
    record(
        f"4 high page count ({pages})",
        pages=pages,
        size_mb=round(len(data) / 1e6, 2),
        outcomes=outcomes,
        step_ms=steps,
        **res.summary(),
    )
    assert outcomes[-1] in ("succeeded", "waiting_for_review"), outcomes


async def test_page_count_above_the_limit_fails_cleanly(
    client, acme, container, ocr_handlers, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    data = files.make_pdf(["x"] * (PROD_MAX_PAGES + 1))
    job_id = uuid.UUID((await upload(client, headers, data)).json()["job_id"])
    outcome = await runner_factory(ocr_handlers(None)).run(job_id)
    row = await job_row(container, job_id)
    assert outcome is RunOutcome.FAILED and row["attempts"] == 1  # not retried
    record(
        "4 page limit exceeded",
        pages=PROD_MAX_PAGES + 1,
        outcome=outcome.value,
        error=row["last_error_code"],
    )


async def test_scanned_page_count_vs_ocr_time_budget(
    client, acme, container, ocr_handlers, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    """Records how scanned page count drives digitize time (OCR is sequential per page).
    With production OCR at ~T s/page, documents above 900/T pages exceed the job lease (F2)."""
    headers = await login(client, acme.owner_email, acme.owner_password)
    pages = 40
    job_id = uuid.UUID((await upload(client, headers, files.scanned_pdf(pages))).json()["job_id"])
    ocr = FaultyOCR(delay_seconds=0.05)
    with Timer() as t:
        outcomes = await _run_to_end(container, runner_factory(ocr_handlers(ocr)), job_id)
    steps = await _step_durations(container, job_id)
    digitize_s = steps["digitize"] / 1000
    overhead_per_page = max(0.0, digitize_s / pages - 0.05)
    record(
        "4/F2 scanned pages vs lease",
        pages=pages,
        simulated_ocr_s_per_page=0.05,
        digitize_s=round(digitize_s, 2),
        non_ocr_s_per_page=round(overhead_per_page, 3),
        max_pages_within_900s_lease_at_10s_ocr=int(900 / (10 + overhead_per_page)),
        max_pages_within_900s_lease_at_30s_ocr=int(900 / (30 + overhead_per_page)),
        outcomes=outcomes,
        wall_s=t.seconds,
    )
    assert outcomes[-1] in ("succeeded", "waiting_for_review")


# 12 -------------------------------------------------------------------------------------
def _png_bomb(side: int = 60_000) -> bytes:
    """A tiny PNG declaring a 60,000 × 60,000 image (3.6 G pixels)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = zlib.compress(b"\x00" * (side // 8 + 1) * 64, 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 1, 0, 0, 0, 0))
        + chunk(b"IDAT", raw)
        + chunk(b"IEND", b"")
    )


def _giant_page_pdf() -> bytes:
    """One page at the PDF maximum (200 × 200 inches): 30,000 × 30,000 px at 150 dpi."""
    return files.make_pdf(["giant"], width=14_400, height=14_400)


@pytest.mark.parametrize(
    ("name", "payload", "content_type"),
    [
        ("png decompression bomb", _png_bomb, "image/png"),
        ("giant page (render bomb)", _giant_page_pdf, "application/pdf"),
        ("corrupted pdf", files.corrupted_pdf, "application/pdf"),
    ],
)
async def test_resource_exhaustion_inputs_fail_safely(
    client, acme, container, ocr_handlers, runner_factory, record, name, payload, content_type
) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    data = payload()
    response = await upload(
        client,
        headers,
        data,
        filename=f"bomb.{content_type.split('/')[1]}",
        content_type=content_type,
    )
    if response.status_code != 201:
        record(f"12 {name}", rejected_at_upload=response.status_code)
        assert response.status_code in (413, 415, 422)
        return
    job_id = uuid.UUID(response.json()["job_id"])
    async with ResourceSampler() as res:
        outcomes = await _run_to_end(container, runner_factory(ocr_handlers(None)), job_id)
    row = await job_row(container, job_id)
    # The worker survives and still processes a normal document afterwards.
    healthy_id = uuid.UUID((await upload(client, headers, files.native_pdf())).json()["job_id"])
    healthy = await runner_factory(ocr_handlers(None)).run(healthy_id)
    record(
        f"12 {name}",
        size_kb=round(len(data) / 1024, 1),
        outcomes=outcomes,
        final=str(row["status"]),
        error=row["last_error_code"],
        worker_still_healthy=healthy.value,
        **res.summary(),
    )
    assert str(row["status"]) in ("FAILED", "DEAD_LETTERED", "WAITING_FOR_REVIEW", "SUCCEEDED")
    assert res.peak_rss < 3 * 1024**3, "process tree exceeded the 3 GiB worker memory limit"
    assert healthy in (RunOutcome.SUCCEEDED, RunOutcome.WAITING_FOR_REVIEW)


# 13 -------------------------------------------------------------------------------------
async def test_cpu_and_ram_under_concurrent_documents(
    client, acme, container, prod_prober, ocr_digitizer, runner_factory, record, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    """WORKER_MAX_JOBS=4 equivalent: 4 jobs at a time over a mixed batch of 12 documents."""
    headers = await login(client, acme.owner_email, acme.owner_password)
    batch = [
        *(
            files.make_pdf([f"Native batch {i} page {p}\nTotal {p}.00" for p in range(60)])
            for i in range(4)
        ),
        *(files.scanned_pdf(8 + i) for i in range(4)),
        *(noise_jpeg_pdf(pages=2, side_px=1800) for _ in range(4)),
    ]
    jobs = [uuid.UUID((await upload(client, headers, d)).json()["job_id"]) for d in batch]
    scratch = tmp_path / "worker-tmp"
    scratch.mkdir()
    ocr_digitizer._ocr = FaultyOCR(delay_seconds=0.05)
    handlers = {
        "probe": ProbeStep(storage=container.storage, prober=prod_prober, tmp_dir=str(scratch)),
        "digitize": DigitizeStep(
            storage=container.storage, digitizer=ocr_digitizer, tmp_dir=str(scratch)
        ),
    }
    slots = asyncio.Semaphore(4)

    async def work(job_id: uuid.UUID) -> list[str]:
        async with slots:
            return await _run_to_end(container, runner_factory(handlers), job_id)

    async with ResourceSampler(scratch=scratch) as res:
        outcomes = await asyncio.gather(*(work(j) for j in jobs))
    finals = [o[-1] for o in outcomes]
    summary = res.summary()
    record(
        "13 CPU/RAM, 12 documents, 4 concurrent",
        documents=len(jobs),
        finals=sorted(set(finals)),
        retries=sum(len(o) - 1 for o in outcomes),
        docs_per_min=round(len(jobs) / summary["wall_s"] * 60, 1),
        **summary,
    )
    assert all(f in ("succeeded", "waiting_for_review") for f in finals), finals
    assert res.peak_rss < 3 * 1024**3
    assert res.peak_scratch < 1024**3  # worker /tmp is a 1 GiB tmpfs in Compose


# F16 (open) / F15 (fixed): deterministic limit breaches must fail once ----------------------
@pytest.mark.xfail(
    strict=True,
    reason="F16: a render that exceeds the parser memory limit (MemoryError in the child) is "
    "classified internal_error (retryable): retried max_attempts times, then dead-lettered, "
    "instead of failing once as a document error",
)
async def test_render_memory_bomb_fails_fast(
    client, acme, container, ocr_handlers, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    job_id = uuid.UUID((await upload(client, headers, _giant_page_pdf())).json()["job_id"])
    outcomes = await _run_to_end(container, runner_factory(ocr_handlers(None)), job_id)
    row = await job_row(container, job_id)
    record(
        "12/F16 render memory bomb",
        outcomes=outcomes,
        final=str(row["status"]),
        error=row["last_error_code"],
        attempts=row["attempts"],
    )
    assert str(row["status"]) == "FAILED" and row["attempts"] == 1


async def test_render_time_budget_breach_fails_fast(
    client, acme, container, prod_prober, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    from idp.providers.digitization.local import HybridDigitizer

    tight = HybridDigitizer(
        ocr=None,
        workers=1,
        timeout_seconds=3,  # whole-document budget
        max_pages=PROD_MAX_PAGES,
        memory_limit_mb=2048,
        render_dpi=150,
        ocr_dpi=300,
        min_native_quality=0.5,
    )
    try:
        headers = await login(client, acme.owner_email, acme.owner_password)
        data = files.make_pdf([f"page {n}" for n in range(80)])  # ~8 s to render > 3 s budget
        job_id = uuid.UUID((await upload(client, headers, data)).json()["job_id"])
        handlers = {
            "probe": ProbeStep(storage=container.storage, prober=prod_prober, tmp_dir=None),
            "digitize": DigitizeStep(storage=container.storage, digitizer=tight, tmp_dir=None),
        }
        with Timer() as t:
            outcomes = await _run_to_end(container, runner_factory(handlers), job_id)
        row = await job_row(container, job_id)
        record(
            "4/F15 render time budget (fixed)",
            pages=80,
            budget_s=3,
            outcomes=outcomes,
            final=str(row["status"]),
            error=row["last_error_code"],
            wall_s=t.seconds,
        )
        assert str(row["status"]) == "FAILED" and row["attempts"] == 1
        assert row["last_error_code"] == "processing_budget_exceeded"
    finally:
        tight.close()
