from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI

from idp.application.auth import principal_from_user
from idp.application.jobs import JobRunner, JobScheduler, JobSweeper
from idp.application.policy import StaticPolicyResolver
from idp.application.steps.classify import ClassifyStep
from idp.application.steps.digitize import DigitizeStep
from idp.application.steps.extract import ExtractStep
from idp.application.steps.probe import ProbeStep
from idp.application.steps.review import ReviewStep
from idp.application.steps.validate import ValidateStep
from idp.application.users import NewUser, TenantBootstrapService, UserService
from idp.application.workflows import StepHandler
from idp.config import Settings
from idp.container import Container
from idp.domain.identity import Role
from idp.domain.routing import PolicySnapshot
from idp.infrastructure.db.repositories import UserRepository
from idp.main import create_app
from idp.providers.digitization.local import HybridDigitizer
from idp.providers.extraction.key_value import KeyValueExtractor
from idp.providers.extraction.regex_extractor import RegexExtractor
from idp.providers.extraction.table import TableExtractor
from idp.providers.probing.local import LocalDocumentProber

OWNER_PASSWORD = "Owner-Password-123!"
USER_PASSWORD = "Member-Password-456!"


@dataclass
class TenantFixture:
    slug: str
    owner_email: str
    owner_password: str = OWNER_PASSWORD


@pytest.fixture
async def app(container: Container, settings: Settings, scheduler: JobScheduler) -> FastAPI:
    application = create_app(settings)
    # Reuse the fixture's container (already truncated + started), with the
    # recording queue so tests can observe dispatches without a worker.
    await application.state.container.close()
    container.scheduler = scheduler
    application.state.container = container
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def bootstrap_tenant(container: Container, slug: str) -> TenantFixture:
    email = f"owner@{slug}.test"
    async with container.session_factory() as session:
        await TenantBootstrapService(session).bootstrap(
            tenant_slug=slug,
            tenant_name=slug.title(),
            owner_email=email,
            owner_name="Owner",
            password=OWNER_PASSWORD,
        )
    return TenantFixture(slug=slug, owner_email=email)


async def add_user(container: Container, owner_email: str, email: str, role: Role) -> None:
    async with container.session_factory() as session:
        owner = await UserRepository(session).find_for_login(owner_email)
        assert owner is not None
        await UserService(session).create_user(
            principal_from_user(owner),
            NewUser(email=email, full_name=email, role=role, password=USER_PASSWORD),
        )


@pytest.fixture
async def acme(container: Container) -> TenantFixture:
    return await bootstrap_tenant(container, "acme")


async def login(client: httpx.AsyncClient, email: str, password: str) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --- Phase 2: documents and job execution ------------------------------------


class RecordingQueue:
    """Test double for the queue port: records dispatches, can simulate outages."""

    def __init__(self) -> None:
        self.messages: list[tuple[uuid.UUID, int, float]] = []
        self.fail = False

    async def enqueue(self, job_id: uuid.UUID, *, token: int, defer_seconds: float = 0) -> None:
        if self.fail:
            raise ConnectionError("queue unavailable")
        self.messages.append((job_id, token, defer_seconds))


@pytest.fixture(scope="session")
def prober() -> Iterator[LocalDocumentProber]:
    p = LocalDocumentProber(workers=1, timeout_seconds=60, max_pages=50, memory_limit_mb=2048)
    yield p
    p.close()


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def scheduler(queue: RecordingQueue, settings: Settings) -> JobScheduler:
    return JobScheduler(queue, settings)


@pytest.fixture
def probe_step(container: Container, prober: LocalDocumentProber) -> ProbeStep:
    return ProbeStep(storage=container.storage, prober=prober, tmp_dir=None)


@pytest.fixture(scope="session")
def digitizer() -> Iterator[HybridDigitizer]:
    # No OCR engine by default: fast and deterministic. OCR has dedicated tests.
    d = HybridDigitizer(
        ocr=None,
        workers=1,
        timeout_seconds=60,
        max_pages=50,
        memory_limit_mb=2048,
        render_dpi=72,
        ocr_dpi=150,
        min_native_quality=0.5,
    )
    yield d
    d.close()


@pytest.fixture
def digitize_step(container: Container, digitizer: HybridDigitizer) -> DigitizeStep:
    return DigitizeStep(storage=container.storage, digitizer=digitizer, tmp_dir=None)


@pytest.fixture
def default_handlers(
    container: Container, probe_step: ProbeStep, digitize_step: DigitizeStep
) -> dict[str, StepHandler]:
    steps: list[StepHandler] = [
        probe_step,
        digitize_step,
        ClassifyStep(storage=container.storage),
        ExtractStep(
            storage=container.storage,
            providers=[RegexExtractor(), KeyValueExtractor(), TableExtractor()],
            policy=StaticPolicyResolver(PolicySnapshot()),
        ),
        ValidateStep(),
        ReviewStep(),
    ]
    return {step.key: step for step in steps}


@pytest.fixture
def make_runner(
    container: Container,
    scheduler: JobScheduler,
    settings: Settings,
    default_handlers: dict[str, StepHandler],
):  # type: ignore[no-untyped-def]
    def _make(handlers: Mapping[str, StepHandler] | None = None) -> JobRunner:
        """Real handlers for every step, with `handlers` overriding specific keys."""
        return JobRunner(
            container.session_factory,
            scheduler,
            {**default_handlers, **(handlers or {})},
            settings,
        )

    return _make


@pytest.fixture
def sweeper(container: Container, scheduler: JobScheduler, settings: Settings) -> JobSweeper:
    return JobSweeper(container.session_factory, scheduler, settings)


async def upload(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    data: bytes,
    filename: str = "invoice.pdf",
    content_type: str = "application/pdf",
) -> httpx.Response:
    return await client.post(
        "/api/v1/documents", headers=headers, files={"file": (filename, data, content_type)}
    )


@pytest.fixture
def pin_workflow(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Create new jobs with a specific built-in workflow version (tests of earlier stages)."""
    from idp.application import jobs as jobs_module
    from idp.application.workflows import get_workflow

    def _pin(version: int) -> None:
        monkeypatch.setattr(jobs_module, "DEFAULT_WORKFLOW", get_workflow("ingest", version))

    return _pin
