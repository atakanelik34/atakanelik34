"""MockOCREngine — DEVELOPMENT ONLY, clearly labelled.

Returns a single fixed marker line so that pipelines can be exercised without
an OCR engine. It never pretends to read the document: the text is always
"[MOCK OCR OUTPUT]" with confidence 0, and `is_mock` is surfaced in the UI.
"""

from __future__ import annotations

from pathlib import Path

from idp.domain.geometry import BBox, Line, Word
from idp.providers.ocr.base import OCRPage

MOCK_TEXT = "[MOCK OCR OUTPUT]"


class MockOCREngine:
    name = "mock-ocr"
    version = "mock"
    is_mock = True
    locality = "local"

    async def recognize(self, image: Path, *, page_number: int) -> OCRPage:
        words = tuple(
            Word(text=t, bbox=BBox(0.05 + i * 0.1, 0.05, 0.14 + i * 0.1, 0.08), confidence=0.0)
            for i, t in enumerate(MOCK_TEXT.split())
        )
        return OCRPage(lines=(Line(id=f"p{page_number}-l0", words=words),), mean_confidence=0.0)
