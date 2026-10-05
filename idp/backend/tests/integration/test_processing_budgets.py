"""F15: coherent time budgets for large documents, and the job lease heartbeat.

The production case was a valid 1,000-page PDF needing ~108 s against a single
120 s whole-document parser timeout: larger documents timed out on every
attempt and were retried until dead-lettered. These tests reproduce that at a
smaller scale (seconds instead of minutes): a whole-document cost that exceeds
a fixed limit, a page that exceeds its per-page budget, and a document that
exceeds the overall digitize budget (render + OCR).
"""

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text

from idp.application.jobs import JobRunner, JobScheduler, RunOutcome
from idp.application.steps.digitize import DigitizeStep
from idp.application.steps.probe import ProbeStep
from idp.application.workflows import StepContext, StepResult
from idp.config import Settings
from idp.container import Container
from idp.domain.geometry import BBox, Line, Word
from idp.infrastructure.db.models import ProcessingJob, ProcessingStep
from idp.providers.digitization.local import HybridDigitizer
from idp.providers.ocr.base import OCRPage
from tests.fixtures import files
from tests.integration.conftest import TenantFixture, login, upload


def _digitizer(
    *, budget: float, page_budget: float, ocr: object | None = None, chunk_pages: int = 10
) -> HybridDigitizer:
    return HybridDigitizer(
        ocr=ocr,  # type: ignore[arg-type]
        workers=1,
        timeout_seconds=budget,
        page_timeout_seconds=page_budget,
        chunk_pages=chunk_pages,
        max_pages=2000,
        memory_limit_mb=2048,
        render_dpi=150,  # production default: ~0.1 s per native page
        ocr_dpi=150,
        min_native_quality=0.5,
    )


class SlowOCR:
    name, version, is_mock, locality = "slow-ocr", "1", True, "local"

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.calls = 0

    async def recognize(self, image: Path, *, page_number: int) -> OCRPage:
        self.calls += 1
        await asyncio.sleep(self.seconds)
        word = Word("text", BBox(0.1, 0.1, 0.2, 0.12), 0.9)
        return OCRPage(lines=(Line(f"p{page_number}-l0", (word,)),), mean_confidence=0.9)


@pytest.fixture
async def owner(client: httpx.AsyncClient, acme: TenantFixture) -> dict[str, str]:
    return await login(client, acme.owner_email, acme.owner_password)


@pytest.fixture
def budgets(container: Container, probe_step: ProbeStep) -> Iterator:  # type: ignore[type-arg]
    made: list[HybridDigitizer] = []

    def _handlers(digitizer: HybridDigitizer) -> dict:  # type: ignore[type-arg]
        made.append(digitizer)
        return {
            "probe": probe_step,
            "digitize": DigitizeStep(storage=container.storage, digitizer=digitizer, tmp_dir=None),
        }

    yield _handlers
    for d in made:
        d.close()


async def _job(container: Container, job_id: uuid.UUID) -> ProcessingJob:
    async with container.session_factory() as session:
        job = await session.get(ProcessingJob, job_id)
        assert job is not None
        return job


async def _run_to_end(runner: JobRunner, container: Container, job_id: uuid.UUID) -> list[str]:
    outcomes = []
    for _ in range(5):
        outcome = await runner.run(job_id)
        outcomes.append(outcome.value)
        if outcome is not RunOutcome.RETRY_SCHEDULED:
            break
        async with container.session_factory() as session:
            await session.execute(
                text("UPDATE processing_jobs SET next_attempt_at = NULL WHERE id = :id"),
                {"id": job_id},
            )
            await session.commit()
    return outcomes


async def test_document_beyond_a_fixed_whole_document_limit_completes_within_its_page_budget(
    client: httpx.AsyncClient, owner: dict[str, str], container: Container, make_runner, budgets
) -> None:  # type: ignore[no-untyped-def]
    """Near/beyond the old boundary: 40 pages need ~4 s, far more than one page's budget
    (0.5 s) and more than a fixed 3 s document limit; each page is within budget, so it
    completes in one attempt."""
    data = files.make_pdf([f"Statement page {n}" for n in range(40)])
    job_id = uuid.UUID((await upload(client, owner, data)).json()["job_id"])
    digitizer = _digitizer(budget=120, page_budget=0.5)
    outcomes = await _run_to_end(make_runner(budgets(digitizer)), container, job_id)
    job = await _job(container, job_id)
    assert outcomes[-1] in {"succeeded", "waiting_for_review"}, outcomes
    assert job.attempts == 1


async def test_page_over_its_render_budget_fails_once_and_deterministically(
    client: httpx.AsyncClient, owner: dict[str, str], container: Container, make_runner, budgets
) -> None:  # type: ignore[no-untyped-def]
    data = files.make_pdf([f"page {n}" for n in range(20)])
    job_id = uuid.UUID((await upload(client, owner, data)).json()["job_id"])
    digitizer = _digitizer(budget=120, page_budget=0.001)  # no page can render this fast
    outcomes = await _run_to_end(make_runner(budgets(digitizer)), container, job_id)
    job = await _job(container, job_id)
    assert outcomes == ["failed"]
    assert (job.attempts, job.last_error_category, job.last_error_code) == (
        1,
        "DOCUMENT_ERROR",
        "processing_budget_exceeded",
    )
    # The pool was recycled: the same digitizer works for the next document.
    other = (await upload(client, owner, files.native_pdf(2))).json()
    digitizer._page_timeout = 5.0
    outcome = await make_runner(budgets(digitizer)).run(uuid.UUID(other["job_id"]))
    assert outcome in {RunOutcome.SUCCEEDED, RunOutcome.WAITING_FOR_REVIEW}


async def test_document_over_the_overall_render_budget_fails_once(
    client: httpx.AsyncClient, owner: dict[str, str], container: Container, make_runner, budgets
) -> None:  # type: ignore[no-untyped-def]
    """Beyond the boundary: 45 pages need ~5 s against a 2 s document budget."""
    data = files.make_pdf([f"page {n}" for n in range(45)])
    job_id = uuid.UUID((await upload(client, owner, data)).json()["job_id"])
    digitizer = _digitizer(budget=2, page_budget=1)
    loop = asyncio.get_running_loop()
    started = loop.time()
    outcomes = await _run_to_end(make_runner(budgets(digitizer)), container, job_id)
    elapsed = loop.time() - started
    job = await _job(container, job_id)
    assert outcomes == ["failed"]
    assert (job.attempts, job.last_error_code) == (1, "processing_budget_exceeded")
    assert elapsed < 4.5  # stopped at the budget, not after rendering everything


async def test_ocr_time_counts_against_the_overall_budget(
    client: httpx.AsyncClient, owner: dict[str, str], container: Container, make_runner, budgets
) -> None:  # type: ignore[no-untyped-def]
    data = files.scanned_pdf(6)
    job_id = uuid.UUID((await upload(client, owner, data)).json()["job_id"])
    ocr = SlowOCR(0.6)  # 6 pages × 0.6 s > 2 s budget
    digitizer = _digitizer(budget=2, page_budget=5, ocr=ocr)
    outcomes = await _run_to_end(make_runner(budgets(digitizer)), container, job_id)
    job = await _job(container, job_id)
    assert outcomes == ["failed"]
    assert (job.attempts, job.last_error_code) == (1, "processing_budget_exceeded")
    assert ocr.calls < 6


# --- lease heartbeat --------------------------------------------------------------------


class SlowStep:
    key = "probe"

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.calls = 0
        self.started = asyncio.Event()

    async def run(self, ctx: StepContext) -> StepResult:
        self.calls += 1
        self.started.set()
        await asyncio.sleep(self.seconds)
        return StepResult(provider="slow", metrics={})


@pytest.fixture
def beating(container: Container, scheduler: JobScheduler, settings: Settings, default_handlers):  # type: ignore[no-untyped-def]
    """Runners with a 1 s lease and a 0.2 s heartbeat; `step` replaces probe."""
    tuned = settings.model_copy(update={"job_lease_seconds": 1, "job_heartbeat_seconds": 0.2})

    def _make(step: SlowStep) -> JobRunner:
        return JobRunner(
            container.session_factory, scheduler, {**default_handlers, "probe": step}, tuned
        )

    return _make


async def test_heartbeat_keeps_the_lease_of_a_step_longer_than_the_lease(
    client: httpx.AsyncClient, owner: dict[str, str], container: Container, beating
) -> None:  # type: ignore[no-untyped-def]
    """A live worker is never presumed dead: no re-claim, no duplicate work, one attempt."""
    job_id = uuid.UUID((await upload(client, owner, files.native_pdf())).json()["job_id"])
    slow = SlowStep(3.0)  # three times the 1 s lease
    first = asyncio.create_task(beating(slow).run(job_id))
    await slow.started.wait()
    contenders = []
    for _ in range(4):
        await asyncio.sleep(0.6)
        contender = beating(SlowStep(0))
        contenders.append(await contender.run(job_id))
    assert set(contenders) == {RunOutcome.NOT_CLAIMED}
    assert await first in {RunOutcome.SUCCEEDED, RunOutcome.WAITING_FOR_REVIEW}
    job = await _job(container, job_id)
    assert (job.attempts, slow.calls) == (1, 1)


async def test_heartbeat_stops_when_the_worker_dies(
    client: httpx.AsyncClient, owner: dict[str, str], container: Container, beating
) -> None:  # type: ignore[no-untyped-def]
    job_id = uuid.UUID((await upload(client, owner, files.native_pdf())).json()["job_id"])
    slow = SlowStep(30.0)
    worker = asyncio.create_task(beating(slow).run(job_id))
    await slow.started.wait()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    lease = (await _job(container, job_id)).lease_expires_at
    await asyncio.sleep(1.5)
    assert (await _job(container, job_id)).lease_expires_at == lease  # nobody renews it
    recovery = beating(SlowStep(0))
    assert await recovery.run(job_id) in {RunOutcome.SUCCEEDED, RunOutcome.WAITING_FOR_REVIEW}
    async with container.session_factory() as session:
        codes = (
            await session.scalars(
                select(ProcessingStep.error_code).where(
                    ProcessingStep.job_id == job_id, ProcessingStep.step_key == "probe"
                )
            )
        ).all()
    assert codes == ["worker_lost", None]
