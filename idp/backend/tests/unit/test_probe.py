from collections.abc import Iterator
from pathlib import Path

import pytest

from idp.domain.documents import PDF, PNG, TIFF
from idp.domain.errors import DocumentError, UnsupportedMediaTypeError
from idp.providers.probing.base import TextLayer
from idp.providers.probing.local import LocalDocumentProber
from tests.fixtures import files


@pytest.fixture(scope="module")
def prober() -> Iterator[LocalDocumentProber]:
    p = LocalDocumentProber(workers=1, timeout_seconds=60, max_pages=5, memory_limit_mb=2048)
    yield p
    p.close()


def _write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "doc"
    path.write_bytes(data)
    return path


async def test_native_pdf_needs_no_ocr(prober: LocalDocumentProber, tmp_path: Path) -> None:
    result = await prober.probe(_write(tmp_path, files.native_pdf(2)), PDF)
    assert result.page_count == 2
    assert result.text_layer is TextLayer.NATIVE
    page = result.pages[0]
    assert (page.width, page.height, page.unit) == (612.0, 792.0, "pt")
    assert page.char_count > 16


async def test_scanned_pdf_is_image_only(prober: LocalDocumentProber, tmp_path: Path) -> None:
    result = await prober.probe(_write(tmp_path, files.scanned_pdf(3)), PDF)
    assert result.text_layer is TextLayer.NONE
    assert [p.has_text_layer for p in result.pages] == [False, False, False]


async def test_hybrid_pdf_is_partial(prober: LocalDocumentProber, tmp_path: Path) -> None:
    data = files.make_pdf(["Cover letter with enough words to count as text", None])
    result = await prober.probe(_write(tmp_path, data), PDF)
    assert result.text_layer is TextLayer.PARTIAL


async def test_images_and_multipage_tiff(prober: LocalDocumentProber, tmp_path: Path) -> None:
    png = await prober.probe(_write(tmp_path, files.png(300, 200)), PNG)
    assert (png.page_count, png.pages[0].unit, png.pages[0].width) == (1, "px", 300.0)
    tiff = await prober.probe(_write(tmp_path, files.multipage_tiff(3)), TIFF)
    assert tiff.page_count == 3


@pytest.mark.parametrize(
    ("data", "mime", "reason"),
    [
        (files.corrupted_pdf(), PDF, "corrupted"),
        (files.native_pdf(6), PDF, "too_many_pages"),
        (files.png()[:60], PNG, "corrupted"),
        (files.multipage_tiff(6), TIFF, "too_many_pages"),
    ],
)
async def test_bad_documents_are_document_errors(
    prober: LocalDocumentProber, tmp_path: Path, data: bytes, mime: str, reason: str
) -> None:
    with pytest.raises(DocumentError) as exc_info:
        await prober.probe(_write(tmp_path, data), mime)
    assert exc_info.value.details["reason"] == reason
    assert not exc_info.value.retryable


async def test_unknown_type_is_rejected(prober: LocalDocumentProber, tmp_path: Path) -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        await prober.probe(_write(tmp_path, b"hello"), "text/plain")
