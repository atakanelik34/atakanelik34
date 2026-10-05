"""Real OCR check: Tesseract inside the default backend image reads scanned pages.

Runs the production digitizer (HybridDigitizer + TesseractOCREngine, production
DPI) on:

* a synthetic scan-like page (`scan_like_pdf`: rendered text, skew, noise, blur,
  JPEG). This proves Tesseract is installed and wired, but says nothing about
  accuracy on real scans;
* every real scan committed in `backend/tests/fixtures/scans/` with its
  `.expected.txt` (see the README there).

CI runs it in the built image (see .github/workflows/idp-ci.yml):

    docker run --rm -v "$PWD/idp:/src:ro" -e PYTHONPATH=/src/backend \\
      -e JWT_SECRET=... idp-backend:ci python /src/scripts/validation/ocr_check.py

Exit status 1 if Tesseract is missing or any expected text is not found.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from idp.domain.documents import JPEG, PDF, PNG, TIFF
from idp.domain.geometry import TextSource
from idp.providers.digitization.local import HybridDigitizer
from idp.providers.ocr.tesseract import TesseractOCREngine
from tests.fixtures.files import scan_like_pdf

SCANS = Path(__file__).resolve().parents[2] / "backend" / "tests" / "fixtures" / "scans"
MIMES = {".pdf": PDF, ".png": PNG, ".jpg": JPEG, ".tif": TIFF}
SYNTHETIC_LINES = [
    "ACME Industrial Supplies GmbH",
    "Invoice INV-2026-0042",
    "Total due 1249.50 EUR",
]
SYNTHETIC_EXPECTED = ["ACME", "INV-2026-0042", "1249.50"]


def cases() -> list[tuple[str, bytes, str, list[str]]]:
    found = [("synthetic scan-like page", scan_like_pdf(SYNTHETIC_LINES), PDF, SYNTHETIC_EXPECTED)]
    if SCANS.is_dir():
        for scan in sorted(SCANS.iterdir()):
            mime = MIMES.get(scan.suffix.lower())
            expected = scan.with_suffix(".expected.txt")
            if mime and expected.exists():
                words = [w.strip() for w in expected.read_text().splitlines() if w.strip()]
                found.append((f"real scan {scan.name}", scan.read_bytes(), mime, words))
    return found


async def main() -> int:
    if shutil.which("tesseract") is None:
        print("FAIL: tesseract is not installed in this image")
        return 1
    engine = TesseractOCREngine(binary="tesseract", languages="eng+deu", timeout_seconds=120)
    await engine.detect_version()
    digitizer = HybridDigitizer(
        ocr=engine,
        workers=1,
        timeout_seconds=1800,
        max_pages=2000,
        memory_limit_mb=2048,
        render_dpi=150,
        ocr_dpi=300,
        min_native_quality=0.5,
    )
    results = []
    try:
        for name, data, mime, expected in cases():
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "input"
                source.write_bytes(data)
                started = time.perf_counter()
                result = await digitizer.digitize(source, mime, Path(tmp))
                seconds = time.perf_counter() - started
            text = "\n".join(p.layout.text for p in result.pages)
            missing = [w for w in expected if w.casefold() not in text.casefold()]
            ocr_pages = sum(1 for p in result.pages if p.layout.source is TextSource.OCR)
            results.append(
                {
                    "case": name,
                    "ok": not missing and ocr_pages > 0,
                    "pages": len(result.pages),
                    "ocr_pages": ocr_pages,
                    "confidence": [p.layout.ocr_confidence for p in result.pages],
                    "missing": missing,
                    "seconds": round(seconds, 2),
                }
            )
    finally:
        digitizer.close()
    print(
        json.dumps({"engine": engine.name, "version": engine.version, "results": results}, indent=2)
    )
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
