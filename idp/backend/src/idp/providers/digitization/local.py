"""Hybrid digitizer: native PDF text where it is good, OCR where it is needed.

Parsing/rendering runs in the isolated process pool (pdfium is not thread-safe
and files are untrusted); OCR runs through the `OCREngine` port. Pages never go
to OCR when their native text layer is readable.

Time budgets (F15) are explicit and nested:

* PDFs are rendered in chunks of `chunk_pages`; each chunk may take at most
  `page_timeout_seconds` per page, so the limit scales with the document instead
  of one fixed whole-document timeout.
* `timeout_seconds` caps the whole document (rendering + OCR). Every chunk and
  every OCR call is also bounded by what is left of it.

Exceeding either budget is a `ProcessingBudgetExceededError`: a document error,
failed once and not retried, because the same document would exceed it again.
The parser pool is recycled so a child stuck on a page cannot keep a slot.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

from idp.domain.documents import JPEG, PDF, PNG, TIFF
from idp.domain.errors import (
    DocumentError,
    IDPError,
    ProcessingBudgetExceededError,
    ProviderError,
    UnsupportedMediaTypeError,
)
from idp.domain.geometry import (
    BBox,
    OcrStatus,
    PageLayout,
    TextSource,
    Word,
    detect_language,
    group_blocks,
    group_lines,
    table_density,
    text_quality,
)
from idp.providers.digitization.extract import extract_image, extract_pdf
from idp.providers.ocr.base import OCREngine
from idp.providers.probing.parsers import init_child


@dataclass(frozen=True, slots=True)
class DigitizedPage:
    layout: PageLayout
    view_image: Path
    view_size: tuple[int, int]
    ocr_status: OcrStatus


@dataclass(frozen=True, slots=True)
class DigitizationResult:
    pages: tuple[DigitizedPage, ...]
    ocr_engine: str | None
    ocr_engine_is_mock: bool
    ocr_seconds: float


class HybridDigitizer:
    name = "hybrid-digitizer"

    def __init__(
        self,
        *,
        ocr: OCREngine | None,
        workers: int,
        timeout_seconds: float,
        max_pages: int,
        memory_limit_mb: int,
        render_dpi: int,
        ocr_dpi: int,
        min_native_quality: float,
        page_timeout_seconds: float = 10.0,
        chunk_pages: int = 10,
    ) -> None:
        self.version = f"pdfium-{version('pypdfium2')}"
        self._ocr = ocr
        self._workers = workers
        self._timeout = timeout_seconds
        self._max_pages = max_pages
        self._memory_limit_mb = memory_limit_mb
        self._render_dpi = render_dpi
        self._ocr_dpi = ocr_dpi
        self._min_quality = min_native_quality
        self._page_timeout = page_timeout_seconds
        self._chunk_pages = chunk_pages
        self._pool = self._new_pool()

    def _new_pool(self) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(
            max_workers=self._workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=init_child,
            initargs=(self._memory_limit_mb,),
        )

    def _recycle_pool(self) -> None:
        """Replace the pool, terminating its children (one may be stuck on a page).

        Other calls still running in the old pool fail with BrokenProcessPool,
        which is transient: their jobs retry within their attempt budget.
        """
        pool, self._pool = self._pool, self._new_pool()
        children = list(getattr(pool, "_processes", {}).values())
        pool.shutdown(wait=False, cancel_futures=True)
        for child in children:
            child.terminate()

    async def _call(self, func: Any, *args: Any, budget: float, reason: str) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        try:
            raw: dict[str, Any] = await asyncio.wait_for(
                loop.run_in_executor(self._pool, func, *args), timeout=max(budget, 0.001)
            )
        except BrokenProcessPool as exc:
            self._recycle_pool()
            raise ProviderError("Document parser process crashed") from exc
        except TimeoutError as exc:
            self._recycle_pool()
            raise ProcessingBudgetExceededError(
                "The document needs more time to digitize than its budget allows",
                details={"reason": reason},
            ) from exc
        if not raw["ok"]:
            raise DocumentError(raw["message"], details={"reason": raw["error"]})
        return raw

    async def _extract(
        self, path: Path, mime: str, workdir: Path, deadline: float
    ) -> list[dict[str, Any]]:
        if mime in (PNG, JPEG, TIFF):
            raw = await self._call(
                extract_image,
                str(path),
                str(workdir),
                self._max_pages,
                budget=deadline - time.monotonic(),
                reason="document_timeout",
            )
            return list(raw["pages"])
        if mime != PDF:
            raise UnsupportedMediaTypeError(f"Cannot digitize {mime}")
        pages: list[dict[str, Any]] = []
        first, total = 0, None
        while total is None or first < total:
            page_budget = self._chunk_pages * self._page_timeout
            remaining = deadline - time.monotonic()
            raw = await self._call(
                extract_pdf,
                str(path),
                str(workdir),
                self._render_dpi,
                self._ocr_dpi,
                self._min_quality,
                self._max_pages,
                first,
                self._chunk_pages,
                budget=min(page_budget, remaining),
                reason="page_timeout" if page_budget <= remaining else "document_timeout",
            )
            pages.extend(raw["pages"])
            total = raw["total"]
            first += self._chunk_pages
        return pages

    async def digitize(self, path: Path, mime: str, workdir: Path) -> DigitizationResult:
        deadline = time.monotonic() + self._timeout
        raw_pages = await self._extract(path, mime, workdir, deadline)
        pages: list[DigitizedPage] = []
        ocr_seconds = 0.0
        for page in raw_pages:
            number = page["page_number"]
            rotation = page["rotation"]
            # pdfium reports page size as displayed (rotation applied).
            displayed_w, displayed_h = page["width"], page["height"]
            ocr_confidence = None
            if page["ocr_image"] is None:
                words = [Word(w["t"], BBox.from_list(w["b"])) for w in page["native_words"]]
                lines = group_lines(words, number)
                source, ocr_status = TextSource.NATIVE, OcrStatus.NOT_NEEDED
            elif self._ocr is None:
                lines, source, ocr_status = [], TextSource.NONE, OcrStatus.NOT_CONFIGURED
            else:
                started = time.perf_counter()
                try:
                    result = await asyncio.wait_for(
                        self._ocr.recognize(Path(page["ocr_image"]), page_number=number),
                        timeout=max(deadline - time.monotonic(), 0.001),
                    )
                except TimeoutError as exc:
                    raise ProcessingBudgetExceededError(
                        "The document needs more time to digitize than its budget allows",
                        details={"reason": "document_timeout"},
                    ) from exc
                except IDPError:
                    raise
                except Exception as exc:  # engine crashed unexpectedly
                    raise ProviderError("OCR engine failed") from exc
                ocr_seconds += time.perf_counter() - started
                lines = list(result.lines)
                ocr_confidence = result.mean_confidence
                source = TextSource.OCR if lines else TextSource.NONE
                ocr_status = OcrStatus.DONE
            text = "\n".join(line.text for line in lines)
            quality = (
                text_quality(text)
                if source is TextSource.NATIVE
                else round(min(text_quality(text), ocr_confidence or 0.0), 3)
            )
            layout = PageLayout(
                page_number=number,
                width=displayed_w,
                height=displayed_h,
                unit=page["unit"],
                rotation=rotation,
                source=source,
                blocks=tuple(group_blocks(lines)),
                text_quality=quality,
                language=detect_language(text),
                ocr_confidence=ocr_confidence,
                table_density=table_density(lines),
                metadata={"native_quality": page["native_quality"]},
            )
            pages.append(
                DigitizedPage(
                    layout=layout,
                    view_image=Path(page["view_image"]),
                    view_size=(page["view_size"][0], page["view_size"][1]),
                    ocr_status=ocr_status,
                )
            )
        return DigitizationResult(
            pages=tuple(pages),
            ocr_engine=self._ocr.name if self._ocr else None,
            ocr_engine_is_mock=bool(self._ocr and self._ocr.is_mock),
            ocr_seconds=round(ocr_seconds, 3),
        )

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)
