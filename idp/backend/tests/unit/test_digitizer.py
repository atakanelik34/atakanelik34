import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from idp.domain.documents import JPEG, PDF, PNG, TIFF
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


SCANS = Path(__file__).resolve().parents[1] / "fixtures" / "scans"


def _real_scans() -> list[Path]:
    return sorted(
        p for p in SCANS.iterdir() if p.suffix.lower() in {".pdf", ".png", ".jpg", ".tif"}
    )


def _scan_pair(scan: Path) -> tuple[list[str], bytes]:
    return scan.with_suffix(".expected.txt").read_text().split("\n"), scan.read_bytes()


@needs_tesseract
async def test_tesseract_reads_a_scan_like_page(tmp_path: Path) -> None:
    """Skew, noise, blur and JPEG artefacts at 300 dpi (synthetic, see fixtures/scans)."""
    engine = TesseractOCREngine(binary="tesseract", languages="eng", timeout_seconds=60)
    await engine.detect_version()
    d = _digitizer(engine)
    try:
        result = await _run(
            d,
            tmp_path,
            files.scan_like_pdf(["Invoice INV-2026-0042", "Total due 1249.50 EUR"]),
            PDF,
        )
    finally:
        d.close()
    page = result.pages[0].layout
    assert page.source is TextSource.OCR
    assert "INV-2026-0042" in page.text and "1249.50" in page.text


@needs_tesseract
@pytest.mark.parametrize("scan", _real_scans(), ids=lambda p: p.name)
async def test_tesseract_reads_committed_real_scans(tmp_path: Path, scan: Path) -> None:
    expected, data = _scan_pair(scan)
    engine = TesseractOCREngine(binary="tesseract", languages="eng+deu", timeout_seconds=120)
    await engine.detect_version()
    d = _digitizer(engine)
    mime = {".pdf": PDF, ".png": PNG, ".jpg": JPEG, ".tif": TIFF}[scan.suffix.lower()]
    try:
        result = await _run(d, tmp_path, data, mime)
    finally:
        d.close()
    text = "\n".join(p.layout.text for p in result.pages).casefold()
    missing = [w for w in expected if w.strip() and w.strip().casefold() not in text]
    assert not missing


def _fake_tesseract(tmp_path: Path) -> Path:
    """Records the OpenMP thread limit it was started with; answers an empty TSV."""
    script = tmp_path / "tesseract"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "${{OMP_THREAD_LIMIT:-unset}}" > {tmp_path}/omp\n'
        'echo "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"\n'
    )
    script.chmod(0o755)
    return script


async def test_tesseract_runs_single_threaded_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pages are OCR'd in parallel (several jobs per worker): OpenMP threads per process
    oversubscribe the worker's CPUs. Measured in phase 13: 4 jobs x 4 threads on a
    2-CPU worker pushed single pages past the 120 s OCR timeout."""
    monkeypatch.delenv("OMP_THREAD_LIMIT", raising=False)
    image = tmp_path / "page.png"
    image.write_bytes(files.png())
    engine = TesseractOCREngine(
        binary=str(_fake_tesseract(tmp_path)), languages="eng", timeout_seconds=10
    )
    await engine.recognize(image, page_number=1)
    assert (tmp_path / "omp").read_text().strip() == "1"
    monkeypatch.setenv("OMP_THREAD_LIMIT", "2")  # an operator's explicit choice wins
    await engine.recognize(image, page_number=1)
    assert (tmp_path / "omp").read_text().strip() == "2"
