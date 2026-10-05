"""Scenarios 15–16: LLM timeout/retry and ERP (webhook action) timeout/retry interactions."""

import asyncio
import json
import uuid
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import select

from idp.application.jobs import RunOutcome
from idp.application.policy import StaticPolicyResolver
from idp.application.steps.action import ActionStep, ApproveActionsStep
from idp.application.steps.extract import ExtractStep
from idp.container import Container
from idp.domain.routing import Locality, PolicySnapshot
from idp.infrastructure.db.models import ActionRun, ProviderCall
from idp.providers.actions.webhook import WebhookActionProvider, WebhookSender
from idp.providers.extraction.key_value import KeyValueExtractor
from idp.providers.extraction.llm import LLMExtractor
from idp.providers.extraction.regex_extractor import RegexExtractor
from idp.providers.extraction.table import TableExtractor
from idp.providers.llm.adapters import OllamaProvider
from idp.providers.llm.gateway import LLMGateway
from idp.providers.resilience import BreakerRegistry
from tests.fixtures import files
from tests.integration.conftest import login, upload
from tests.integration.test_actions import _configure
from tests.integration.validation.conftest import Timer, job_row, make_due

ANSWER = {
    "fields": {"vendor_name": {"value": "ACME Industrial Supplies GmbH", "line": "p1-l0"}},
    "rows": {},
}


class ModelServer:
    """Scripted model server: each call runs the next behaviour (default: answer)."""

    def __init__(self, *behaviours: Callable[[], object]) -> None:
        self.behaviours = list(behaviours)
        self.calls = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.behaviours:
            outcome = self.behaviours.pop(0)()
            if asyncio.iscoroutine(outcome):
                await outcome
        return httpx.Response(
            200,
            json={
                "message": {"content": json.dumps(ANSWER)},
                "prompt_eval_count": 100,
                "eval_count": 10,
            },
        )


def timeout() -> None:
    raise httpx.ReadTimeout("model server timed out")


async def slow_then_timeout() -> None:
    await asyncio.sleep(0.8)
    raise httpx.ReadTimeout("model server timed out")


def _extract(
    container: Container,
    server: ModelServer,
    *,
    provider_timeout: float = 30.0,
    breakers: BreakerRegistry | None = None,
) -> ExtractStep:
    llm = OllamaProvider(
        name="ollama",
        base_url="http://ollama:11434",
        model="test-model",
        declared_locality=Locality.LOCAL,
        timeout_seconds=5,
        client=httpx.AsyncClient(transport=httpx.MockTransport(server)),
    )
    gateway = LLMGateway(
        {"ollama": llm},
        allowed_hosts=frozenset({"ollama"}),
        local_hosts=frozenset({"ollama"}),
        max_retries=2,
        backoff_seconds=0.2,
    )
    return ExtractStep(
        storage=container.storage,
        providers=[
            RegexExtractor(),
            KeyValueExtractor(),
            TableExtractor(),
            LLMExtractor(gateway, "ollama", max_input_chars=10_000),
        ],
        policy=StaticPolicyResolver(PolicySnapshot()),
        provider_timeout_seconds=provider_timeout,
        breakers=breakers,
    )


@pytest.fixture
async def owner(client, acme):  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    await client.post(
        "/api/v1/document-types/from-template",
        headers=headers,
        json={"template_key": "invoice", "publish": True},
    )
    return headers


async def _process(client, headers, runner, data: bytes) -> tuple[RunOutcome, dict]:  # type: ignore[no-untyped-def]
    body = (await upload(client, headers, data)).json()
    outcome = await runner.run(uuid.UUID(body["job_id"]))
    result = (
        await client.get(f"/api/v1/documents/{body['document']['id']}/extraction", headers=headers)
    ).json()
    return outcome, result


def _llm_attempt(result: dict) -> dict:
    stages = result["parts"][0]["extraction"]["route_trace"]["stages"]
    return next(a for s in stages for a in s["attempts"] if a["provider"] == "llm:ollama")


# 15 -------------------------------------------------------------------------------------
async def test_llm_transient_timeouts_are_retried_within_the_call(
    client, owner, container, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    server = ModelServer(timeout, timeout)
    outcome, result = await _process(
        client, owner, runner_factory({"extract": _extract(container, server)}), files.invoice_pdf()
    )
    assert outcome in {RunOutcome.SUCCEEDED, RunOutcome.WAITING_FOR_REVIEW}
    assert _llm_attempt(result)["status"] == "ok"
    assert result["parts"][0]["fields"]["vendor_name"]["provider"] == "llm:ollama"
    async with container.session_factory() as session:
        [call] = (await session.scalars(select(ProviderCall))).all()
    assert (call.status, call.attempts) == ("ok", 3)
    record(
        "15 LLM timeouts retried",
        server_calls=server.calls,
        attempts=call.attempts,
        outcome=outcome.value,
    )


async def test_llm_retries_are_bounded_by_the_provider_timeout(
    client, owner, container, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    """Gateway retries (3 × 0.8 s + backoff) outlast the extract provider timeout (1.5 s):
    the attempt is cut at the timeout, the document goes to review, the job is not failed."""
    server = ModelServer(slow_then_timeout, slow_then_timeout, slow_then_timeout)
    runner = runner_factory({"extract": _extract(container, server, provider_timeout=1.5)})
    with Timer() as t:
        outcome, result = await _process(client, owner, runner, files.invoice_pdf())
    attempt = _llm_attempt(result)
    assert attempt["status"] == "timeout"
    assert outcome is RunOutcome.WAITING_FOR_REVIEW  # deterministic results kept, human decides
    assert attempt["duration_ms"] < 2500
    record(
        "15 LLM retries vs provider timeout",
        attempt=attempt,
        server_calls=server.calls,
        job_wall_s=t.seconds,
    )


@pytest.mark.xfail(
    strict=True,
    reason="F13: an LLM call cancelled by the extract provider timeout leaves no provider_calls "
    "record, so usage/cost of abandoned calls is not metered",
)
async def test_llm_call_cut_by_the_provider_timeout_is_still_metered(
    client, owner, container, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    server = ModelServer(slow_then_timeout, slow_then_timeout, slow_then_timeout)
    await _process(
        client,
        owner,
        runner_factory({"extract": _extract(container, server, provider_timeout=1.5)}),
        files.invoice_pdf(),
    )
    async with container.session_factory() as session:
        calls = (await session.scalars(select(ProviderCall))).all()
    record(
        "15 abandoned LLM call metering", server_calls=server.calls, provider_call_rows=len(calls)
    )
    assert calls, "no usage record for a call that reached the model server"


async def test_llm_breaker_opens_after_repeated_timeouts(
    client, owner, container, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    server = ModelServer(*([timeout] * 30))
    breakers = BreakerRegistry(failure_threshold=2, reset_after_seconds=300)
    runner = runner_factory({"extract": _extract(container, server, breakers=breakers)})
    statuses = []
    for i in range(4):
        rows = [list(r) for r in files.INVOICE_ROWS]
        rows[5] = [(72, f"Invoice number: INV-2026-{900 + i}")]
        _, result = await _process(client, owner, runner, files.invoice_pdf(rows))
        trace = result["parts"][0]["extraction"]["route_trace"]
        rejected = {r["provider"]: r["reason"] for r in trace["rejected"]}
        statuses.append(rejected.get("llm:ollama") or _llm_attempt(result)["status"])
    assert statuses[:2] == ["failed", "failed"]
    assert all("circuit open" in s for s in statuses[2:])
    record("15 LLM breaker", per_document=statuses, server_calls=server.calls)


# 16 -------------------------------------------------------------------------------------
class ErpReceiver:
    def __init__(self, delays: list[float], status: int = 201) -> None:
        self.delays = delays
        self.status = status
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        delay = self.delays.pop(0) if self.delays else 0.0
        await asyncio.sleep(delay)
        return httpx.Response(self.status, json={"id": "ERP-1001"})

    def provider(self) -> WebhookActionProvider:
        sender = WebhookSender(
            allowed_hosts=frozenset({"hooks.example.test"}),
            client=httpx.AsyncClient(transport=httpx.MockTransport(self)),
        )
        return WebhookActionProvider(sender)


def _action_steps(receiver: ErpReceiver, timeout_seconds: float) -> dict:
    providers = {"webhook": receiver.provider()}
    policy = StaticPolicyResolver(PolicySnapshot())
    return {
        "approve_actions": ApproveActionsStep(providers=providers, policy=policy),
        "action": ActionStep(providers=providers, policy=policy, timeout_seconds=timeout_seconds),
    }


async def _run_run(container: Container, runner, job_id: uuid.UUID) -> RunOutcome:  # type: ignore[no-untyped-def]
    outcome = await runner.run(job_id)
    if outcome is RunOutcome.RETRY_SCHEDULED:
        await make_due(container, job_id)
    return outcome


async def _action_run(container: Container) -> ActionRun:
    async with container.session_factory() as session:
        [run] = (await session.scalars(select(ActionRun))).all()
    return run


@pytest.fixture
async def erp_owner(client, acme, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("IDP_SECRET_HOOK", "hook-secret")
    headers = await login(client, acme.owner_email, acme.owner_password)
    await _configure(client, headers, kind="webhook", requires_approval=False)
    return headers


async def test_erp_timeout_is_retried_with_the_same_idempotency_key(
    client, erp_owner, container, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    receiver = ErpReceiver(delays=[2.0])  # first call outlasts the 0.5 s action timeout
    runner = runner_factory(_action_steps(receiver, timeout_seconds=0.5))
    job_id = uuid.UUID(
        (await upload(client, erp_owner, files.clean_invoice_pdf())).json()["job_id"]
    )
    first = await _run_run(container, runner, job_id)
    run = await _action_run(container)
    assert first is RunOutcome.RETRY_SCHEDULED
    assert (run.status, run.attempts, run.error_code) == ("approved", 1, "timeout")
    second = await runner.run(job_id)
    run = await _action_run(container)
    assert second is RunOutcome.SUCCEEDED
    assert (run.status, run.attempts, run.external_reference) == ("succeeded", 2, "ERP-1001")
    keys = {r.headers["Idempotency-Key"] for r in receiver.requests}
    assert len(receiver.requests) == 2 and len(keys) == 1
    # A further delivery of the job never posts again.
    assert await runner.run(job_id) is RunOutcome.NOT_CLAIMED
    record(
        "16 ERP timeout then success",
        outcomes=[first.value, second.value],
        receiver_requests=len(receiver.requests),
        distinct_keys=len(keys),
    )


async def test_erp_timeouts_exhaust_retries_into_dead_letter(
    client, erp_owner, container, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    receiver = ErpReceiver(delays=[2.0] * 10)
    runner = runner_factory(_action_steps(receiver, timeout_seconds=0.3))
    body = (await upload(client, erp_owner, files.clean_invoice_pdf())).json()
    job_id = uuid.UUID(body["job_id"])
    outcomes = []
    for _ in range(6):
        outcome = await _run_run(container, runner, job_id)
        outcomes.append(outcome.value)
        if outcome is not RunOutcome.RETRY_SCHEDULED:
            break
    row = await job_row(container, job_id)
    run = await _action_run(container)
    doc = (
        await client.get(f"/api/v1/documents/{body['document']['id']}", headers=erp_owner)
    ).json()
    assert str(row["status"]) == "DEAD_LETTERED"
    assert run.status != "succeeded" and run.attempts == row["max_attempts"]
    assert doc["status"] == "FAILED"
    assert len({r.headers["Idempotency-Key"] for r in receiver.requests}) == 1
    record(
        "16 ERP timeouts exhaust retries",
        outcomes=outcomes,
        run_status=run.status,
        run_attempts=run.attempts,
        receiver_requests=len(receiver.requests),
        document=doc["status"],
    )


async def test_erp_rejection_is_not_retried(
    client, erp_owner, container, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    receiver = ErpReceiver(delays=[], status=400)
    runner = runner_factory(_action_steps(receiver, timeout_seconds=5))
    job_id = uuid.UUID(
        (await upload(client, erp_owner, files.clean_invoice_pdf())).json()["job_id"]
    )
    assert await runner.run(job_id) is RunOutcome.FAILED
    run = await _action_run(container)
    assert (run.status, run.error_code, len(receiver.requests)) == ("failed", "http_400", 1)
    record("16 ERP rejection", run_status=run.status, error=run.error_code)


@pytest.mark.xfail(
    strict=True,
    reason="F14: replaying a job dead-lettered by ERP timeouts creates a new action run with a "
    "new idempotency key (key = job:part:action), so an ERP that did process a timed-out "
    "request cannot deduplicate the replay",
)
async def test_replay_after_erp_timeouts_keeps_the_idempotency_key(
    client, erp_owner, container, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    receiver = ErpReceiver(delays=[2.0] * 3)
    runner = runner_factory(_action_steps(receiver, timeout_seconds=0.3))
    body = (await upload(client, erp_owner, files.clean_invoice_pdf())).json()
    job_id = uuid.UUID(body["job_id"])
    for _ in range(4):
        if await _run_run(container, runner, job_id) is not RunOutcome.RETRY_SCHEDULED:
            break
    replay = await client.post(
        f"/api/v1/documents/{body['document']['id']}/process", headers=erp_owner
    )
    await runner.run(uuid.UUID(replay.json()["id"]))
    keys = [r.headers["Idempotency-Key"] for r in receiver.requests]
    record("16 replay after ERP timeouts", requests=len(keys), distinct_keys=len(set(keys)))
    assert len(set(keys)) == 1
