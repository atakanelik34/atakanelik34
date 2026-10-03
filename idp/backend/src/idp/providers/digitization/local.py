"""Hybrid digitizer: native PDF text where it is good, OCR where it is needed.

Parsing/rendering runs in the isolated process pool (pdfium is not thread-safe
and files are untrusted); OCR runs through the `OCREngine` port. Pages never go
to OCR when their native text layer is readable.
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
from idp.domain.errors import DocumentError, IDPError, ProviderError, UnsupportedMediaTypeError
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
        self._pool = self._new_pool()

    def _new_pool(self) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(
            max_workers=self._workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=init_child,
            initargs=(self._memory_limit_mb,),
        )

    async def _extract(self, path: Path, mime: str, workdir: Path) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        if mime == PDF:
            call = loop.run_in_executor(
                self._pool,
                extract_pdf,
                str(path),
                str(workdir),
                self._render_dpi,
                self._ocr_dpi,
                self._min_quality,
                self._max_pages,
            )
        elif mime in (PNG, JPEG, TIFF):
            call = loop.run_in_executor(
                self._pool, extract_image, str(path), str(workdir), self._max_pages
            )
        else:
            raise UnsupportedMediaTypeError(f"Cannot digitize {mime}")
        try:
            raw: dict[str, Any] = await asyncio.wait_for(call, timeout=self._timeout)
        except BrokenProcessPool as exc:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = self._new_pool()
            raise ProviderError("Document parser process crashed") from exc
        except TimeoutError as exc:
            raise ProviderError("Digitization timed out") from exc
        if not raw["ok"]:
            raise DocumentError(raw["message"], details={"reason": raw["error"]})
        return raw

    async def digitize(self, path: Path, mime: str, workdir: Path) -> DigitizationResult:
        raw = await self._extract(path, mime, workdir)
        pages: list[DigitizedPage] = []
        ocr_seconds = 0.0
        for page in raw["pages"]:
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
                    result = await self._ocr.recognize(Path(page["ocr_image"]), page_number=number)
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
