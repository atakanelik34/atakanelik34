from __future__ import annotations

from idp.config import Settings
from idp.providers.ocr.base import OCREngine
from idp.providers.ocr.mock import MockOCREngine
from idp.providers.ocr.tesseract import TesseractOCREngine


def create_ocr_engine(settings: Settings) -> OCREngine | None:
    """None means "not configured": image-only pages are recorded as such, never faked.

    A configured engine that cannot start (e.g. missing binary) raises at worker
    startup rather than silently degrading.
    """
    if settings.ocr_engine == "tesseract":
        return TesseractOCREngine(
            binary=settings.tesseract_cmd,
            languages=settings.ocr_languages,
            timeout_seconds=settings.ocr_timeout_seconds,
        )
    if settings.ocr_engine == "mock":
        return MockOCREngine()
    return None
