"""Production validation suite: fault injection against the real pipeline.

Runs the real runner, lease, retry, sweeper and step code on real Postgres and
Redis; only the *edges* are faked (OCR engine, model server, ERP receiver) so
faults — slowness, crashes, timeouts — can be injected deterministically.

Run:  pytest -m validation            (excluded from the default test run)
Every test records its measurements; the session writes them to
$VALIDATION_REPORT (default: validation-report.json in the working directory).
Known defects are encoded as strict xfail tests: they fail the run as soon as
the defect is fixed, so the expectation gets updated instead of going stale.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from idp.application.jobs import JobRunner, JobScheduler
from idp.application.steps.digitize import DigitizeStep
from idp.application.steps.probe import ProbeStep
from idp.application.workflows import StepHandler
from idp.config import Settings
from idp.container import Container
from idp.domain.errors import ProviderError
from idp.domain.geometry import BBox, Line, Word
from idp.providers.digitization.local import HybridDigitizer
from idp.providers.ocr.base import OCRPage
from idp.providers.probing.local import LocalDocumentProber

VALIDATION_DIR = Path(__file__).resolve().parent
_RESULTS: list[dict[str, Any]] = []

# Production defaults for document limits (the shared test fixtures use 50 pages).
PROD_MAX_PAGES = 2000
PROD_TIMEOUT_SECONDS = 120.0


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.is_relative_to(VALIDATION_DIR):
            item.add_marker(pytest.mark.validation)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _RESULTS:
        return
    target = Path(os.environ.get("VALIDATION_REPORT", "validation-report.json"))
    target.write_text(
        json.dumps(
            {"generated_at": datetime.now(UTC).isoformat(), "results": _RESULTS},
            indent=2,
            default=str,
        )
    )


@pytest.fixture
def record(request: pytest.FixtureRequest):  # type: ignore[no-untyped-def]
    """Record measurements for the validation report: record(scenario=..., **values)."""

    def _record(scenario: str, **values: Any) -> None:
        _RESULTS.append({"test": request.node.nodeid, "scenario": scenario, **values})

    return _record


# --- fault-injecting OCR -----------------------------------------------------------------


@dataclass
class FaultyOCR:
    """An OCR engine whose speed and failures are scripted per call."""

    delay_seconds: float = 0.0
    fail_on_calls: set[int] = field(default_factory=set)  # 1-based call numbers
    name: str = "faulty-ocr"
    version: str = "test"
    is_mock: bool = True
    locality: str = "local"
    calls: int = 0
    started: asyncio.Event = field(default_factory=asyncio.Event)

    async def recognize(self, image: Path, *, page_number: int) -> OCRPage:
        self.calls += 1
        self.started.set()
        if self.calls in self.fail_on_calls:
            raise ProviderError("OCR engine unavailable")
        await asyncio.sleep(self.delay_seconds)
        words = (Word("Invoice", BBox(0.1, 0.1, 0.3, 0.13), 0.95),)
        return OCRPage(lines=(Line(id=f"p{page_number}-l0", words=words),), mean_confidence=0.95)


@pytest.fixture(scope="session")
def prod_prober() -> Iterator[LocalDocumentProber]:
    p = LocalDocumentProber(
        workers=2,
        timeout_seconds=PROD_TIMEOUT_SECONDS,
        max_pages=PROD_MAX_PAGES,
        memory_limit_mb=2048,
    )
    yield p
    p.close()


@pytest.fixture(scope="session")
def ocr_digitizer() -> Iterator[HybridDigitizer]:
    """Production limits; the OCR engine is swapped per test (attribute `_ocr`)."""
    d = HybridDigitizer(
        ocr=None,
        workers=2,
        timeout_seconds=PROD_TIMEOUT_SECONDS,
        max_pages=PROD_MAX_PAGES,
        memory_limit_mb=2048,
        render_dpi=150,  # production default
        ocr_dpi=300,  # production default
        min_native_quality=0.5,
    )
    yield d
    d.close()


@pytest.fixture
def ocr_handlers(
    container: Container, prod_prober: LocalDocumentProber, ocr_digitizer: HybridDigitizer
):  # type: ignore[no-untyped-def]
    """Handlers for probe + digitize with a given OCR engine (others from defaults)."""

    def _make(engine: FaultyOCR | None) -> dict[str, StepHandler]:
        ocr_digitizer._ocr = engine
        return {
            "probe": ProbeStep(storage=container.storage, prober=prod_prober, tmp_dir=None),
            "digitize": DigitizeStep(
                storage=container.storage, digitizer=ocr_digitizer, tmp_dir=None
            ),
        }

    return _make


@pytest.fixture
def runner_factory(
    container: Container, scheduler: JobScheduler, settings: Settings, default_handlers
):  # type: ignore[no-untyped-def]
    """A JobRunner with handler overrides and settings overrides (e.g. a short lease)."""

    def _make(handlers: dict[str, StepHandler] | None = None, **overrides: Any) -> JobRunner:
        tuned = settings.model_copy(update=overrides) if overrides else settings
        return JobRunner(
            container.session_factory, scheduler, {**default_handlers, **(handlers or {})}, tuned
        )

    return _make


# --- database helpers --------------------------------------------------------------------


async def job_row(container: Container, job_id: uuid.UUID) -> dict[str, Any]:
    async with container.session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, attempts, max_attempts, lease_expires_at, last_error_code "
                    "FROM processing_jobs WHERE id = :id"
                ),
                {"id": job_id},
            )
        ).one()
    return dict(row._mapping)


async def expire_lease(container: Container, job_id: uuid.UUID) -> None:
    async with container.session_factory() as session:
        await session.execute(
            text(
                "UPDATE processing_jobs SET lease_expires_at = now() - interval '1 second' "
                "WHERE id = :id"
            ),
            {"id": job_id},
        )
        await session.commit()


async def make_due(container: Container, job_id: uuid.UUID) -> None:
    """Skip the retry backoff: the scheduled retry is due now."""
    async with container.session_factory() as session:
        await session.execute(
            text("UPDATE processing_jobs SET next_attempt_at = NULL WHERE id = :id"),
            {"id": job_id},
        )
        await session.commit()


async def steps_of(container: Container, job_id: uuid.UUID) -> list[tuple[str, str, str | None]]:
    async with container.session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT step_key, status, error_code FROM processing_steps "
                    "WHERE job_id = :id ORDER BY started_at"
                ),
                {"id": job_id},
            )
        ).all()
    return [(r[0], str(r[1]), r[2]) for r in rows]


class Timer:
    def __enter__(self) -> Timer:
        self.started = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.seconds = round(time.perf_counter() - self.started, 3)


# --- large documents and resource sampling --------------------------------------------------


def noise_jpeg_pdf(pages: int, side_px: int = 2600, quality: int = 92) -> bytes:
    """A 'scanned' PDF of incompressible noise images: large on disk, no text layer."""
    import io

    from PIL import Image

    jpegs = []
    for _ in range(pages):
        img = Image.frombytes("RGB", (side_px, side_px), os.urandom(side_px * side_px * 3))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        jpegs.append(buf.getvalue())
    objects: list[bytes] = []
    kids = " ".join(f"{3 + i * 3} 0 R" for i in range(pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    for i, data in enumerate(jpegs):
        image, content = 4 + i * 3, 5 + i * 3
        size = 612  # points; the image is scaled onto a letter-width page
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {size} {size}] "
            f"/Resources << /XObject << /Im{i} {image} 0 R >> >> /Contents {content} 0 R >>".encode()
        )
        objects.append(
            f"<< /Type /XObject /Subtype /Image /Width {side_px} /Height {side_px} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode()
            + data
            + b"\nendstream"
        )
        draw = f"q {size} 0 0 {size} 0 0 cm /Im{i} Do Q".encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(draw) + draw + b"\nendstream")
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def _proc_tree(root: int) -> list[int]:
    parents: dict[int, int] = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                with open(f"/proc/{entry}/stat") as fh:
                    fields = fh.read().rsplit(")", 1)[1].split()
                parents[int(entry)] = int(fields[1])
            except (OSError, IndexError, ValueError):
                continue
    tree, frontier = [root], [root]
    while frontier:
        current = frontier.pop()
        children = [pid for pid, ppid in parents.items() if ppid == current]
        tree.extend(children)
        frontier.extend(children)
    return tree


def _rss_and_cpu(pids: list[int]) -> tuple[int, float]:
    rss, cpu = 0, 0.0
    ticks = os.sysconf("SC_CLK_TCK")
    for pid in pids:
        try:
            with open(f"/proc/{pid}/status") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        rss += int(line.split()[1]) * 1024
            with open(f"/proc/{pid}/stat") as fh:
                fields = fh.read().rsplit(")", 1)[1].split()
                cpu += (int(fields[11]) + int(fields[12])) / ticks
        except (OSError, IndexError, ValueError):
            continue
    return rss, cpu


def _dir_bytes(path: Path) -> int:
    total = 0
    for dirpath, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return total


class ResourceSampler:
    """Samples RSS of this process tree (worker + parser pools), CPU and a scratch dir."""

    def __init__(self, scratch: Path | None = None, interval: float = 0.2) -> None:
        self.scratch = scratch
        self.interval = interval
        self.peak_rss = 0
        self.peak_scratch = 0
        self.samples = 0
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        while True:
            rss, _ = _rss_and_cpu(_proc_tree(os.getpid()))
            self.peak_rss = max(self.peak_rss, rss)
            if self.scratch is not None:
                self.peak_scratch = max(self.peak_scratch, _dir_bytes(self.scratch))
            self.samples += 1
            await asyncio.sleep(self.interval)

    async def __aenter__(self) -> ResourceSampler:
        self.baseline_rss, self._cpu0 = _rss_and_cpu(_proc_tree(os.getpid()))
        self._wall0 = time.perf_counter()
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._task is not None
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        _, cpu1 = _rss_and_cpu(_proc_tree(os.getpid()))
        self.wall_s = round(time.perf_counter() - self._wall0, 2)
        self.cpu_s = round(cpu1 - self._cpu0, 2)

    def summary(self) -> dict[str, Any]:
        mib = 1024 * 1024
        return {
            "wall_s": self.wall_s,
            "cpu_s": self.cpu_s,
            "avg_cores_busy": round(self.cpu_s / self.wall_s, 2) if self.wall_s else None,
            "baseline_rss_mib": round(self.baseline_rss / mib),
            "peak_rss_mib": round(self.peak_rss / mib),
            "peak_scratch_mib": round(self.peak_scratch / mib, 1),
        }
