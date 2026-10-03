import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from idp.domain.documents import PDF, PNG
from idp.domain.errors import DocumentError
from idp.domain.geometry import OcrStatus, TextSource
from idp.providers.digitization.local import HybridDigitizer
from idp.providers.ocr.base import OCRPage
from idp.providers.ocr.mock import MOCK_TEXT, MockOCREngine
from idp.providers.ocr.tesseract import TesseractOCREngine
from tests.fixtures import files

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract not installed"
)


class SpyOCR:
    name, version, is_mock, locality = "spy", "1", False, "local"

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def recognize(self, image: Path, *, page_number: int) -> OCRPage:
        self.calls.append(page_number)
        return OCRPage(lines=(), mean_confidence=None)


def _digitizer(ocr: object | None) -> HybridDigitizer:
    return HybridDigitizer(
        ocr=ocr,  # type: ignore[arg-type]
        workers=1,
        timeout_seconds=60,
        max_pages=20,
        memory_limit_mb=2048,
        render_dpi=72,
        ocr_dpi=200,
        min_native_quality=0.5,
    )


@pytest.fixture
def spy() -> SpyOCR:
    return SpyOCR()


@pytest.fixture
def spy_digitizer(spy: SpyOCR) -> Iterator[HybridDigitizer]:
    d = _digitizer(spy)
    yield d
    d.close()


async def _run(d: HybridDigitizer, tmp_path: Path, data: bytes, mime: str):  # type: ignore[no-untyped-def]
    src = tmp_path / "in"
    src.write_bytes(data)
    work = tmp_path / "work"
    work.mkdir()
    return await d.digitize(src, mime, work)


async def test_native_pdf_is_never_ocrd(
    spy_digitizer: HybridDigitizer, spy: SpyOCR, tmp_path: Path
) -> None:
    result = await _run(spy_digitizer, tmp_path, files.native_pdf(2), PDF)
    assert spy.calls == []
    page = result.pages[0]
    assert page.layout.source is TextSource.NATIVE
    assert page.ocr_status is OcrStatus.NOT_NEEDED
    assert "INV-2026-00123" in page.layout.text
    assert page.layout.language == "en" or page.layout.language is None
    word = next(w for w in page.layout.words if w.text == "INV-2026-00123")
    assert 0.05 < word.bbox.y0 < 0.15  # near the top of the page
    assert page.view_image.exists()


async def test_only_image_pages_go_to_ocr(
    spy_digitizer: HybridDigitizer, spy: SpyOCR, tmp_path: Path
) -> None:
    data = files.make_pdf(["Cover letter with a proper amount of readable words", None, None])
    await _run(spy_digitizer, tmp_path, data, PDF)
    assert spy.calls == [2, 3]


async def test_without_ocr_engine_pages_are_marked_not_configured(tmp_path: Path) -> None:
    d = _digitizer(None)
    try:
        result = await _run(d, tmp_path, files.scanned_pdf(1), PDF)
    finally:
        d.close()
    page = result.pages[0]
    assert (page.layout.source, page.ocr_status) == (TextSource.NONE, OcrStatus.NOT_CONFIGURED)
    assert page.layout.text == ""
    assert result.ocr_engine is None


async def test_mock_ocr_is_labelled_and_never_reads_the_document(tmp_path: Path) -> None:
    d = _digitizer(MockOCREngine())
    try:
        result = await _run(d, tmp_path, files.image_pdf("ACME INVOICE 4711"), PDF)
    finally:
        d.close()
    assert result.ocr_engine_is_mock
    assert result.pages[0].layout.text == MOCK_TEXT
    assert result.pages[0].layout.ocr_confidence == 0.0


async def test_rotated_page_geometry_stays_on_page(
    spy_digitizer: HybridDigitizer, tmp_path: Path
) -> None:
    result = await _run(
        spy_digitizer, tmp_path, files.make_pdf(["Rotated page text here"], rotate=90), PDF
    )
    page = result.pages[0]
    assert (page.layout.width, page.layout.height, page.layout.rotation) == (792, 612, 90)
    assert page.view_size[0] > page.view_size[1]  # rendered landscape
    assert all(0 <= v <= 1 for w in page.layout.words for v in w.bbox.as_list())


async def test_corrupted_input_is_a_document_error(
    spy_digitizer: HybridDigitizer, tmp_path: Path
) -> None:
    with pytest.raises(DocumentError):
        await _run(spy_digitizer, tmp_path, files.corrupted_pdf(), PDF)


@needs_tesseract
async def test_tesseract_reads_scanned_pdf_and_images(tmp_path: Path) -> None:
    engine = TesseractOCREngine(binary="tesseract", languages="eng", timeout_seconds=60)
    await engine.detect_version()
    assert engine.version != "unknown"
    d = _digitizer(engine)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    try:
        scanned = await _run(d, tmp_path / "a", files.image_pdf("ACME INVOICE 4711"), PDF)
        image = await _run(d, tmp_path / "b", files.text_image("TOTAL 1250.00 EUR"), PNG)
    finally:
        d.close()
    page = scanned.pages[0]
    assert page.layout.source is TextSource.OCR
    assert "INVOICE" in page.layout.text and "4711" in page.layout.text
    assert page.layout.ocr_confidence and page.layout.ocr_confidence > 0.6
    assert "1250.00" in image.pages[0].layout.text
