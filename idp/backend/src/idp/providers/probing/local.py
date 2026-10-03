"""Local probe provider backed by pdfium (PDF) and Pillow (images).

Parsing untrusted files happens in a separate process pool:
* pdfium is not thread-safe, so threads are not an option;
* a parser crash or memory blow-up kills a child, not the worker;
* each child has an address-space limit.
Full sandboxing (seccomp / separate container) is a phase-12 item.
"""

from __future__ import annotations

import asyncio
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from importlib.metadata import version
from pathlib import Path
from typing import Any

from idp.domain.documents import JPEG, PDF, PNG, TIFF
from idp.domain.errors import DocumentError, ProviderError, UnsupportedMediaTypeError
from idp.providers.probing.base import PageProbe, ProbeResult
from idp.providers.probing.parsers import init_child, probe_image, probe_pdf

_PARSERS = {PDF: probe_pdf, PNG: probe_image, JPEG: probe_image, TIFF: probe_image}


class LocalDocumentProber:
    name = "pdfium-pillow-probe"

    def __init__(
        self, *, workers: int, timeout_seconds: float, max_pages: int, memory_limit_mb: int
    ) -> None:
        self.version = f"pdfium-{version('pypdfium2')}+pillow-{version('pillow')}"
        self._workers = workers
        self._timeout = timeout_seconds
        self._max_pages = max_pages
        self._memory_limit_mb = memory_limit_mb
        self._pool = self._new_pool()

    def _new_pool(self) -> ProcessPoolExecutor:
        # "spawn": children start clean instead of forking an asyncio process.
        return ProcessPoolExecutor(
            max_workers=self._workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=init_child,
            initargs=(self._memory_limit_mb,),
        )

    async def probe(self, path: Path, mime_type: str) -> ProbeResult:
        parser = _PARSERS.get(mime_type)
        if parser is None:
            raise UnsupportedMediaTypeError(f"No probe available for {mime_type}")
        loop = asyncio.get_running_loop()
        try:
            raw: dict[str, Any] = await asyncio.wait_for(
                loop.run_in_executor(self._pool, parser, str(path), self._max_pages),
                timeout=self._timeout,
            )
        except BrokenProcessPool as exc:
            # A child died (crash or memory limit). Replace the pool; the job's
            # bounded retries decide whether this document is a poison pill.
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = self._new_pool()
            raise ProviderError("Document parser process crashed") from exc
        except TimeoutError as exc:
            raise ProviderError("Document probe timed out") from exc

        if not raw["ok"]:
            raise DocumentError(raw["message"], details={"reason": raw["error"]})
        return ProbeResult(pages=tuple(PageProbe(**page) for page in raw["pages"]))

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)
