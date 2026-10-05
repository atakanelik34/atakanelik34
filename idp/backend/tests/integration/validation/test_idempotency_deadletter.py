"""Scenarios 10–11: duplicate processing / idempotency, dead-letter behaviour."""

import asyncio
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text

from idp.application.jobs import JobSweeper, RunOutcome
from idp.application.workflows import StepContext, StepResult
from idp.domain.errors import DocumentError, ProviderError
from tests.fixtures import files
from tests.integration.conftest import login, upload
from tests.integration.validation.conftest import job_row, make_due, steps_of


@dataclass
class FailingStep:
    """Stands in for any step whose dependency keeps failing."""

    key: str
    error: Exception
    calls: int = 0

    async def run(self, ctx: StepContext) -> StepResult:
        self.calls += 1
        raise self.error


# 10 -------------------------------------------------------------------------------------
async def test_concurrent_deliveries_of_one_job_run_it_once(
    client, acme, container, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    job_id = uuid.UUID((await upload(client, headers, files.native_pdf())).json()["job_id"])
    outcomes = await asyncio.gather(*(runner_factory().run(job_id) for _ in range(5)))
    finished = [o for o in outcomes if o is not RunOutcome.NOT_CLAIMED]
    assert len(finished) == 1
    assert outcomes.count(RunOutcome.NOT_CLAIMED) == 4
    # A redelivered message after completion is a no-op too.
    assert await runner_factory().run(job_id) is RunOutcome.NOT_CLAIMED
    steps = await steps_of(container, job_id)
    assert len([s for s in steps if s[0] == "probe"]) == 1
    record("10 concurrent duplicate deliveries", deliveries=5, executions=len(finished))


async def _identical_uploads(client, headers, data: bytes, n: int) -> list[int | str]:  # type: ignore[no-untyped-def]
    results = await asyncio.gather(
        *(upload(client, headers, data) for _ in range(n)), return_exceptions=True
    )
    return [
        r.status_code if not isinstance(r, BaseException) else type(r).__name__ for r in results
    ]


async def test_concurrent_identical_uploads_keep_one_document(
    client, acme, container, record
) -> None:  # type: ignore[no-untyped-def]
    """Whatever the race outcome, the database never holds two copies."""
    headers = await login(client, acme.owner_email, acme.owner_password)
    statuses = await _identical_uploads(client, headers, files.native_pdf(pages=2), 20)
    async with container.session_factory() as session:
        documents = await session.scalar(text("SELECT count(*) FROM documents"))
        jobs = await session.scalar(text("SELECT count(*) FROM processing_jobs"))
    assert documents == 1 and jobs == 1
    assert statuses.count(201) == 1
    record(
        "10 concurrent identical uploads",
        uploads=20,
        documents=documents,
        jobs=jobs,
        statuses=sorted(map(str, set(statuses))),
    )


@pytest.mark.xfail(
    strict=True,
    reason="F12: losing the duplicate-upload race reads an expired ORM object after rollback "
    "(MissingGreenlet) and answers 500 instead of 409",
)
async def test_concurrent_identical_uploads_answer_409_not_500(
    client, acme, record, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """The race loser path, made deterministic: each request's duplicate pre-check misses
    (as when identical uploads pass it before either commits), so every request after the
    first reaches the unique-constraint path. Timing alone no longer reproduces it reliably
    since the upload rate-limit check (F1) added a Redis round-trip before ingestion."""
    from idp.application.ingestion import IngestionService

    original = IngestionService._find_duplicate

    async def lost_race(self, *args):  # type: ignore[no-untyped-def]
        calls = getattr(self, "_race_calls", 0)
        self._race_calls = calls + 1
        return None if calls == 0 else await original(self, *args)

    monkeypatch.setattr(IngestionService, "_find_duplicate", lost_race)
    headers = await login(client, acme.owner_email, acme.owner_password)
    data = files.native_pdf(pages=3)
    statuses = [(await upload(client, headers, data)).status_code for _ in range(3)]
    statuses += await _identical_uploads(client, headers, data, 10)
    record("10 identical uploads: race losers", statuses=sorted(map(str, set(statuses))))
    assert all(s in (201, 409) for s in statuses), statuses


# 11 -------------------------------------------------------------------------------------
async def test_retryable_failures_dead_letter_and_replay_recovers(
    client, acme, container, scheduler, settings, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    body = (await upload(client, headers, files.native_pdf())).json()
    job_id, doc_id = uuid.UUID(body["job_id"]), body["document"]["id"]
    broken = FailingStep("classify", ProviderError("dependency down"))
    runner = runner_factory({"classify": broken})
    outcomes = []
    for _ in range(10):
        outcome = await runner.run(job_id)
        outcomes.append(outcome.value)
        if outcome is not RunOutcome.RETRY_SCHEDULED:
            break
        await make_due(container, job_id)
    row = await job_row(container, job_id)
    assert str(row["status"]) == "DEAD_LETTERED"
    assert row["attempts"] == row["max_attempts"] == broken.calls
    doc = (await client.get(f"/api/v1/documents/{doc_id}", headers=headers)).json()
    assert doc["status"] == "FAILED"
    # Earlier steps were checkpointed: probe/digitize ran once across all attempts.
    steps = await steps_of(container, job_id)
    assert len([s for s in steps if s[0] == "probe"]) == 1
    # The sweeper never resurrects a dead-lettered job.
    assert await JobSweeper(container.session_factory, scheduler, settings).sweep() == 0
    # Operator replays once the dependency is back.
    replay = await client.post(f"/api/v1/documents/{doc_id}/process", headers=headers)
    assert replay.status_code in (200, 201, 202)
    assert await runner_factory().run(uuid.UUID(replay.json()["id"])) in {
        RunOutcome.SUCCEEDED,
        RunOutcome.WAITING_FOR_REVIEW,
    }
    audit = (
        await client.get("/api/v1/audit-logs?action=job.dead_lettered", headers=headers)
    ).json()
    record(
        "11 dead-letter + replay",
        outcomes=outcomes,
        attempts=row["attempts"],
        dead_letter_audited=bool(audit["items"]),
    )


async def test_non_retryable_failure_fails_immediately(
    client, acme, container, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    job_id = uuid.UUID((await upload(client, headers, files.native_pdf())).json()["job_id"])
    broken = FailingStep("classify", DocumentError("unreadable"))
    assert await runner_factory({"classify": broken}).run(job_id) is RunOutcome.FAILED
    assert broken.calls == 1
    record("11 non-retryable", attempts=(await job_row(container, job_id))["attempts"])
