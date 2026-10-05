"""Tesseract OCR adapter (local). Uses the CLI's TSV output; no Python binding."""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import os
import shutil
from pathlib import Path

from PIL import Image

from idp.domain.errors import ConfigurationError, ProviderError
from idp.domain.geometry import BBox, Line, Word
from idp.providers.ocr.base import OCRPage

_MIN_WORD_CONFIDENCE = 0  # tesseract uses -1 for non-word rows


class TesseractOCREngine:
    name = "tesseract"
    is_mock = False
    locality = "local"

    def __init__(self, *, binary: str, languages: str, timeout_seconds: float) -> None:
        resolved = shutil.which(binary)
        if resolved is None:
            raise ConfigurationError(f"Tesseract binary '{binary}' not found")
        self._binary = resolved
        self._languages = languages
        self._timeout = timeout_seconds
        self.version = "unknown"

    @staticmethod
    def _env() -> dict[str, str]:
        """One OpenMP thread per tesseract process unless the operator says otherwise.

        Pages and jobs already run in parallel; Tesseract's default of one thread
        per core oversubscribes the worker (phase 13: 4 jobs x 4 threads on a 2-CPU
        worker pushed single pages past the OCR timeout).
        """
        return {**os.environ, "OMP_THREAD_LIMIT": os.environ.get("OMP_THREAD_LIMIT", "1")}

    async def detect_version(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            self._binary,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env(),
        )
        out, _ = await proc.communicate()
        first = out.decode(errors="replace").splitlines()[:1]
        self.version = first[0].replace("tesseract ", "") if first else "unknown"
        return self.version

    async def recognize(self, image: Path, *, page_number: int) -> OCRPage:
        with Image.open(image) as img:
            width, height = img.size
        proc = await asyncio.create_subprocess_exec(
            self._binary,
            str(image),
            "stdout",
            "-l",
            self._languages,
            "--psm",
            "3",
            "tsv",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env(),
        )
        try:
            out, _err = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            raise ProviderError("OCR timed out") from exc
        if proc.returncode != 0:
            raise ProviderError("OCR engine failed")
        return parse_tsv(out.decode("utf-8", errors="replace"), width, height, page_number)


def parse_tsv(tsv: str, width: int, height: int, page_number: int) -> OCRPage:
    """Turn tesseract TSV into lines of words with normalised boxes."""
    groups: dict[tuple[int, int, int], list[Word]] = {}
    confidences: list[float] = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            continue
        if row.get("level") != "5" or not text or conf < _MIN_WORD_CONFIDENCE:
            continue
        left, top = int(row["left"]), int(row["top"])
        w, h = int(row["width"]), int(row["height"])
        word = Word(
            text=text,
            bbox=BBox.clamp(left / width, top / height, (left + w) / width, (top + h) / height),
            confidence=round(conf / 100, 3),
        )
        key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
        groups.setdefault(key, []).append(word)
        confidences.append(conf / 100)
    ordered = sorted(
        groups.values(), key=lambda ws: (min(w.bbox.y0 for w in ws), min(w.bbox.x0 for w in ws))
    )
    lines = tuple(
        Line(id=f"p{page_number}-l{i}", words=tuple(sorted(ws, key=lambda w: w.bbox.x0)))
        for i, ws in enumerate(ordered)
    )
    mean = round(sum(confidences) / len(confidences), 3) if confidences else None
    return OCRPage(lines=lines, mean_confidence=mean)
